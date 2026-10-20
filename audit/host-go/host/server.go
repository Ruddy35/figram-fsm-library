package host

import (
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"os"
	"regexp"
	"strconv"
	"sync"
	"time"
	"unicode/utf8"
)

var (
	userRe  = regexp.MustCompile(`^[A-Za-z0-9_-]{1,64}$`)
	eventRe = regexp.MustCompile(`^(cmd|btn|msg|payment):[a-z0-9_]{1,48}$`)
)

// RejectErr: the update is malformed or forged; acknowledged, never processed.
type RejectErr struct{ Why string }

func (r *RejectErr) Error() string { return "rejected: " + r.Why }

// ValidateUpdate parses a Telegram-like update. Policy (documented):
// valid UTF-8 JSON object, no duplicate keys; user [A-Za-z0-9_-]{1,64};
// update_id plain integer 0..2^53-1; event (cmd|btn|msg|payment):[a-z0-9_]{1,48}
// (timer:* only comes from our own scheduler); no "token" field; optional arg:
// string of ≤ 4096 characters without NUL. Unknown extra fields are ignored
// (forward compatibility with new Telegram fields).
func ValidateUpdate(body []byte, noValidate bool) (Update, error) {
	v, err := ParseStrict(body)
	if err != nil {
		return Update{}, &RejectErr{"not valid JSON: " + err.Error()}
	}
	obj, ok := v.(map[string]any)
	if !ok {
		return Update{}, &RejectErr{"update is not an object"}
	}
	user, ok := obj["user"].(string)
	if !ok || !userRe.MatchString(user) {
		return Update{}, &RejectErr{"bad user"}
	}
	uid, ok := AsUint53(obj["update_id"])
	if !ok {
		return Update{}, &RejectErr{"bad update_id"}
	}
	ev, ok := obj["event"].(string)
	u := Update{User: user, UID: "tg:" + strconv.FormatInt(uid, 10), Event: ev}
	if noValidate {
		if !ok || ev == "" {
			return Update{}, &RejectErr{"bad event"}
		}
		if t, ok := AsUint53(obj["token"]); ok {
			u.Token = &t // BROKEN: token accepted from Telegram
		}
		if a, ok := obj["arg"].(string); ok {
			u.Arg = &a
		}
		return u, nil
	}
	if !ok || !eventRe.MatchString(ev) {
		return Update{}, &RejectErr{"bad event (timer:* and unknown prefixes are not accepted from Telegram)"}
	}
	if _, ok := obj["token"]; ok {
		return Update{}, &RejectErr{"token is not accepted from Telegram"}
	}
	if a, present := obj["arg"]; present {
		s, ok := a.(string)
		if !ok || utf8.RuneCountInString(s) > 4096 {
			return Update{}, &RejectErr{"bad arg"}
		}
		for i := 0; i < len(s); i++ {
			if s[i] == 0 {
				return Update{}, &RejectErr{"arg contains NUL"}
			}
		}
		u.Arg = &s
	}
	return u, nil
}

// once-only HTTP responder (lets a broken variant answer early).
type responder struct {
	mu   sync.Mutex
	w    http.ResponseWriter
	done bool
	t0   time.Time
}

func (r *responder) send(code int, body map[string]any) {
	r.mu.Lock()
	defer r.mu.Unlock()
	if r.done {
		return
	}
	r.done = true
	body["lat_us"] = time.Since(r.t0).Microseconds()
	b, _ := json.Marshal(body)
	r.w.Header().Set("Content-Type", "application/json")
	r.w.WriteHeader(code)
	r.w.Write(b)
	if f, ok := r.w.(http.Flusher); ok {
		f.Flush()
	}
}

func (h *Host) reject(body []byte, why string) {
	txt := body
	if len(txt) > 2000 {
		txt = txt[:2000]
	}
	for i := 0; i < 20; i++ {
		if _, err := h.db.Exec(`INSERT INTO rejected(body,reason,t_ms) VALUES(?,?,?)`, string(txt), why, h.now()); err == nil {
			return
		}
		time.Sleep(50 * time.Millisecond)
	}
}

func (h *Host) handleUpdate(w http.ResponseWriter, req *http.Request) {
	h.inflightN.Add(1)
	defer h.inflightN.Add(-1)
	rs := &responder{w: w, t0: time.Now()}
	body, err := io.ReadAll(io.LimitReader(req.Body, 1<<20))
	if err != nil {
		rs.send(503, map[string]any{"why": "read: " + err.Error()})
		return
	}
	u, err := ValidateUpdate(body, h.broken["no-validate"])
	if err != nil {
		h.reject(body, err.Error())
		h.stat("rejected", 1)
		rs.send(200, map[string]any{"status": "rejected", "why": err.Error()})
		return
	}
	t1 := time.Now()
	st, err := h.Step(u, func(status string) { rs.send(200, map[string]any{"status": status, "early": true}) })
	stepUs := time.Since(t1).Microseconds()
	if err != nil {
		h.stat("nack", 1)
		rs.send(503, map[string]any{"why": err.Error()})
		return
	}
	rs.send(200, map[string]any{"status": st, "step_us": stepUs})
}

func (h *Host) handleCtl(w http.ResponseWriter, req *http.Request) {
	rs := &responder{w: w, t0: time.Now()}
	body, _ := io.ReadAll(req.Body)
	var cmd map[string]any
	dec := json.NewDecoder(bytesReader(body))
	dec.UseNumber()
	if err := dec.Decode(&cmd); err != nil {
		rs.send(400, map[string]any{"error": err.Error()})
		return
	}
	if cmd["ctl"] == "stop" {
		err := h.Barrier(120 * time.Second)
		st := h.StatsReport()
		if err != nil {
			st["barrier_error"] = err.Error()
		}
		rs.send(200, st)
		go func() {
			time.Sleep(50 * time.Millisecond)
			h.Close()
			os.Exit(0)
		}()
		return
	}
	out, err := h.Ctl(cmd)
	if err != nil {
		rs.send(500, map[string]any{"error": err.Error()})
		return
	}
	if out == nil {
		out = map[string]any{}
	}
	rs.send(200, out)
}

// Serve listens on a unix socket and prints READY <pid> when accepting.
func (h *Host) Serve(sock string) error {
	os.Remove(sock)
	ln, err := net.Listen("unix", sock)
	if err != nil {
		return err
	}
	mux := http.NewServeMux()
	mux.HandleFunc("/update", h.handleUpdate)
	mux.HandleFunc("/ctl", h.handleCtl)
	mux.HandleFunc("/stats", func(w http.ResponseWriter, r *http.Request) {
		b, _ := json.Marshal(h.StatsReport())
		w.Write(b)
	})
	srv := &http.Server{Handler: mux, ReadHeaderTimeout: 10 * time.Second}
	fmt.Printf("READY %d\n", os.Getpid())
	os.Stdout.Sync()
	err = srv.Serve(ln)
	if errors.Is(err, http.ErrServerClosed) {
		return nil
	}
	return err
}

func bytesReader(b []byte) io.Reader { return &sliceReader{b: b} }

type sliceReader struct{ b []byte }

func (s *sliceReader) Read(p []byte) (int, error) {
	if len(s.b) == 0 {
		return 0, io.EOF
	}
	n := copy(p, s.b)
	s.b = s.b[n:]
	return n, nil
}

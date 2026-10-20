package main

// Simulated external services (CRM/helpdesk/calendar and Telegram Bot API), served
// over a unix socket by the runner process — so they survive a kill -9 of the host
// exactly like remote services would. Never touches the network.
//
// CRM: every operation carries Idempotency-Key; the first request applies the effect
// and stores the result, later ones return the stored result (Stripe-like); same key
// with a different fingerprint is answered with the stored result and recorded as a
// key conflict (ET-01 symptom).
// Telegram: NO idempotency (sendMessage has no key) — every successful request is a
// delivery; the outbox key arrives only as a debug header for the checker.

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"math/rand"
	"net"
	"net/http"
	"os"
	"strconv"
	"strings"
	"sync"
	"time"
)

type extMode struct {
	Target    string `json:"target"`
	Mode      string `json:"mode"`
	Count     int    `json:"count"`
	Remaining int    `json:"remaining"`
	Ms        int    `json:"ms"`
	Line      int    `json:"line"`
}

type extReq struct {
	Target  string `json:"target"`
	Key     string `json:"key"`
	User    string `json:"user"`
	Op      string `json:"op"`
	Mode    string `json:"mode"`
	Outcome string `json:"outcome"`
}

type crmEffect struct {
	Key    string         `json:"key"`
	User   string         `json:"user"`
	Op     string         `json:"op"`
	FP     string         `json:"fp"`
	Result map[string]any `json:"result"`
	Seq    int            `json:"seq"`
	Payload any           `json:"payload"`
}

type tgDelivery struct {
	MessageID int    `json:"message_id"`
	Key       string `json:"key"`
	User      string `json:"user"`
	Text      string `json:"text"`
}

type ticket struct {
	ID       int64
	User     string
	State    string
	Messages int
}

// ExtSim is the simulator state.
type ExtSim struct {
	mu        sync.Mutex
	modes     []*extMode
	Requests  []extReq
	Effects   map[string]*crmEffect
	effSeq    int
	Conflicts []map[string]string
	tickets   map[int64]*ticket
	nextTkt   int64
	nextOrder int64
	nextEvent int64
	TG        []tgDelivery
	Reorders  [][]int
	rng       *rand.Rand

	rmu    sync.Mutex
	batch  []*pending
	timerOn bool
	srv    *http.Server
	ln     net.Listener
}

type pending struct {
	fn   func()
	done chan struct{}
}

func NewExtSim(seed int64) *ExtSim {
	return &ExtSim{Effects: map[string]*crmEffect{}, tickets: map[int64]*ticket{}, rng: rand.New(rand.NewSource(seed))}
}

// SetMode is called by the runner ("the outside world").
func (x *ExtSim) SetMode(target, mode string, count, ms, line int) {
	x.mu.Lock()
	defer x.mu.Unlock()
	if mode == "ok" {
		for _, m := range x.modes {
			if target == "all" || m.Target == target {
				m.Remaining = 0
			}
		}
		return
	}
	x.modes = append(x.modes, &extMode{Target: target, Mode: mode, Count: count, Remaining: count, Ms: ms, Line: line})
}

func (x *ExtSim) take(target string) (string, int) {
	x.mu.Lock()
	defer x.mu.Unlock()
	for _, m := range x.modes {
		if m.Remaining > 0 && (m.Target == target || m.Target == "all") {
			m.Remaining--
			return m.Mode, m.Ms
		}
	}
	return "ok", 0
}

func (x *ExtSim) logReq(r extReq) {
	x.mu.Lock()
	x.Requests = append(x.Requests, r)
	x.mu.Unlock()
}

func fingerprint(user, op string, payload any) string {
	b, _ := json.Marshal([]any{user, op, payload})
	s := sha256.Sum256(b)
	return hex.EncodeToString(s[:8])
}

// reorder: concurrent requests are collected (up to 3 or 40 ms) and applied in a
// shuffled order that always differs from arrival order.
func (x *ExtSim) reordered(fn func()) {
	p := &pending{fn: fn, done: make(chan struct{})}
	x.rmu.Lock()
	x.batch = append(x.batch, p)
	if len(x.batch) >= 3 {
		x.flushLocked()
	} else if !x.timerOn {
		x.timerOn = true
		time.AfterFunc(40*time.Millisecond, func() {
			x.rmu.Lock()
			x.flushLocked()
			x.rmu.Unlock()
		})
	}
	x.rmu.Unlock()
	<-p.done
}

func (x *ExtSim) flushLocked() {
	b := x.batch
	x.batch = nil
	x.timerOn = false
	if len(b) == 0 {
		return
	}
	x.mu.Lock()
	order := x.rng.Perm(len(b))
	if len(b) > 1 {
		sorted := true
		for i, v := range order {
			if v != i {
				sorted = false
			}
		}
		if sorted {
			for i, j := 0, len(order)-1; i < j; i, j = i+1, j-1 {
				order[i], order[j] = order[j], order[i]
			}
		}
		x.Reorders = append(x.Reorders, order)
	}
	x.mu.Unlock()
	for _, i := range order {
		b[i].fn()
		close(b[i].done)
	}
}

type permanentErr struct{ msg string }

func (p *permanentErr) Error() string { return p.msg }

func (x *ExtSim) crmApply(op, key, user string, payload map[string]any, mode string) (map[string]any, error) {
	fp := fingerprint(user, op, payload)
	x.mu.Lock()
	defer x.mu.Unlock()
	if e, ok := x.Effects[key]; ok {
		if e.FP != fp {
			x.Conflicts = append(x.Conflicts, map[string]string{"key": key, "user": user, "op": op, "fp_old": e.FP, "fp_new": fp})
		}
		x.Requests = append(x.Requests, extReq{"crm", key, user, op, mode, "dedup"})
		return e.Result, nil
	}
	var res map[string]any
	switch op {
	case "create_order":
		x.nextOrder++
		res = map[string]any{"order_id": x.nextOrder}
	case "ticket_create":
		x.nextTkt++
		x.tickets[x.nextTkt] = &ticket{ID: x.nextTkt, User: user, State: "open", Messages: 1}
		res = map[string]any{"ticket_id": x.nextTkt}
	case "ticket_message", "ticket_escalate", "ticket_close":
		var tid int64
		switch v := payload["ticket_id"].(type) {
		case float64:
			tid = int64(v)
		}
		t := x.tickets[tid]
		if t == nil || t.User != user {
			x.Requests = append(x.Requests, extReq{"crm", key, user, op, mode, "permanent"})
			return nil, &permanentErr{fmt.Sprintf("no ticket %v for %s", payload["ticket_id"], user)}
		}
		switch op {
		case "ticket_message":
			t.Messages++
		case "ticket_escalate":
			t.State = "escalated"
		default:
			t.State = "closed"
		}
		res = map[string]any{"ticket_id": tid}
	case "calendar":
		x.nextEvent++
		res = map[string]any{"event_id": x.nextEvent}
	case "notify":
		res = map[string]any{"notified": true}
	default:
		return nil, &permanentErr{"unknown op " + op}
	}
	x.effSeq++
	x.Effects[key] = &crmEffect{Key: key, User: user, Op: op, FP: fp, Result: res, Seq: x.effSeq, Payload: payload}
	x.Requests = append(x.Requests, extReq{"crm", key, user, op, mode, "applied"})
	return res, nil
}

func (x *ExtSim) tgApply(key, user, text, mode string) map[string]any {
	x.mu.Lock()
	defer x.mu.Unlock()
	id := len(x.TG) + 1
	x.TG = append(x.TG, tgDelivery{MessageID: id, Key: key, User: user, Text: text})
	x.Requests = append(x.Requests, extReq{"tg", key, user, "send", mode, "delivered"})
	return map[string]any{"message_id": id}
}

func dropConn(w http.ResponseWriter) {
	if hj, ok := w.(http.Hijacker); ok {
		if c, _, err := hj.Hijack(); err == nil {
			c.Close()
			return
		}
	}
	panic(http.ErrAbortHandler)
}

func (x *ExtSim) handle(target string, w http.ResponseWriter, r *http.Request, apply func(mode string) (map[string]any, error), key, user, op string) {
	mode, ms := x.take(target)
	switch mode {
	case "500":
		x.logReq(extReq{target, key, user, op, mode, "500"})
		w.WriteHeader(500)
		return
	case "429":
		if ms == 0 {
			ms = 20
		}
		x.logReq(extReq{target, key, user, op, mode, "429"})
		w.Header().Set("Retry-After-Ms", strconv.Itoa(ms))
		w.WriteHeader(429)
		return
	case "timeout_before":
		x.logReq(extReq{target, key, user, op, mode, "timeout_before"})
		dropConn(w)
		return
	case "delay":
		time.Sleep(time.Duration(ms) * time.Millisecond)
	}
	var res map[string]any
	var err error
	if mode == "reorder" {
		x.reordered(func() { res, err = apply(mode) })
	} else {
		res, err = apply(mode)
	}
	if err != nil {
		w.WriteHeader(422)
		io.WriteString(w, err.Error())
		return
	}
	if mode == "dup" {
		apply("dup2") // the network delivered the request a second time
	}
	if mode == "timeout" {
		dropConn(w) // applied, response lost
		return
	}
	b, _ := json.Marshal(map[string]any{"result": res})
	w.Header().Set("Content-Type", "application/json")
	w.Write(b)
}

// Serve starts the HTTP server on a unix socket.
func (x *ExtSim) Serve(sock string) error {
	os.Remove(sock)
	ln, err := net.Listen("unix", sock)
	if err != nil {
		return err
	}
	mux := http.NewServeMux()
	mux.HandleFunc("/crm/", func(w http.ResponseWriter, r *http.Request) {
		op := strings.TrimPrefix(r.URL.Path, "/crm/")
		key := r.Header.Get("Idempotency-Key")
		var body struct {
			User    string         `json:"user"`
			Payload map[string]any `json:"payload"`
		}
		json.NewDecoder(r.Body).Decode(&body)
		x.handle("crm", w, r, func(mode string) (map[string]any, error) {
			return x.crmApply(op, key, body.User, body.Payload, mode)
		}, key, body.User, op)
	})
	mux.HandleFunc("/tg/send", func(w http.ResponseWriter, r *http.Request) {
		key := r.Header.Get("X-Outbox-Key")
		var body struct {
			User string `json:"user"`
			Text string `json:"text"`
		}
		json.NewDecoder(r.Body).Decode(&body)
		x.handle("tg", w, r, func(mode string) (map[string]any, error) {
			return x.tgApply(key, body.User, body.Text, mode), nil
		}, key, body.User, "send")
	})
	x.ln = ln
	x.srv = &http.Server{Handler: mux}
	go x.srv.Serve(ln)
	return nil
}

func (x *ExtSim) Close() {
	if x.srv != nil {
		x.srv.Close()
	}
}

// ModesLeft returns modes with remaining quota (unconsumed fault injections).
func (x *ExtSim) ModesLeft() []extMode {
	x.mu.Lock()
	defer x.mu.Unlock()
	var out []extMode
	for _, m := range x.modes {
		if m.Remaining > 0 {
			out = append(out, *m)
		}
	}
	return out
}

// Counts summarises the simulator state.
func (x *ExtSim) Counts() map[string]any {
	x.mu.Lock()
	defer x.mu.Unlock()
	byOutcome := map[string]int{}
	modes := map[string]int{}
	for _, r := range x.Requests {
		byOutcome[r.Target+":"+r.Outcome]++
		if r.Mode != "ok" {
			modes[r.Mode]++
		}
	}
	perKey := map[string]int{}
	for _, d := range x.TG {
		perKey[d.Key]++
	}
	dupKeys := 0
	for _, n := range perKey {
		if n > 1 {
			dupKeys++
		}
	}
	return map[string]any{"requests": len(x.Requests), "by_outcome": byOutcome, "fault_modes_seen": modes,
		"crm_effects": len(x.Effects), "key_conflicts": len(x.Conflicts), "tg_deliveries": len(x.TG),
		"tg_duplicate_keys": dupKeys, "reordered_batches": len(x.Reorders)}
}

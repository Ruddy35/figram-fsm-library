package main

// Corpus runner: plays "Telegram" (delivers updates, redelivers until acknowledged),
// the operator (control lines) and the fault injector (real kill -9 of the host
// process at armed points and restart from the same DB, DB lock held by another
// connection, external fault modes, engine reply faults, corrupted snapshots written
// straight into the DB).

import (
	"bufio"
	"bytes"
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"sync/atomic"
	"syscall"
	"time"

	_ "modernc.org/sqlite"
)

const maxInt53 = 9007199254740991

// Item is one corpus line.
type Item struct {
	Line int
	Raw  map[string]any
	Text []byte
}

func (it Item) str(k string) string { s, _ := it.Raw[k].(string); return s }
func (it Item) num(k string) int64 {
	switch v := it.Raw[k].(type) {
	case float64:
		return int64(v)
	case json.Number:
		n, _ := v.Int64()
		return n
	}
	return 0
}
func (it Item) has(k string) bool { _, ok := it.Raw[k]; return ok }

func loadCorpus(path string) ([]Item, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	var out []Item
	sc := bufio.NewScanner(f)
	sc.Buffer(make([]byte, 1<<20), 16<<20)
	n := 0
	for sc.Scan() {
		line := bytes.TrimSpace(sc.Bytes())
		if len(line) == 0 {
			continue
		}
		var m map[string]any
		dec := json.NewDecoder(bytes.NewReader(line))
		dec.UseNumber()
		if err := dec.Decode(&m); err != nil {
			return nil, fmt.Errorf("line %d: %v", n, err)
		}
		out = append(out, Item{Line: n, Raw: m, Text: append([]byte{}, line...)})
		n++
	}
	return out, sc.Err()
}

var faultCtls = map[string]bool{"crash": true, "restart_host": true, "db_lock": true, "engine_fault": true, "external": true}

// stripClean: the same corpus without fault lines, duplicates and delays.
func stripClean(items []Item) []Item {
	var out []Item
	for _, it := range items {
		if faultCtls[it.str("ctl")] {
			continue
		}
		m := map[string]any{}
		for k, v := range it.Raw {
			if k == "dup" || k == "delay_ms" {
				continue
			}
			m[k] = v
		}
		if it.str("ctl") == "timer_mode" && len(m) == 1 {
			continue
		}
		out = append(out, Item{Line: it.Line, Raw: m, Text: it.Text})
	}
	return out
}

// stripPristine: clean and additionally without malformed updates and corruptions.
func stripPristine(items []Item) []Item {
	var out []Item
	for _, it := range stripClean(items) {
		if it.has("raw") || it.has("raw_text") || it.str("ctl") == "corrupt_snapshot" {
			continue
		}
		out = append(out, it)
	}
	return out
}

type Opts struct {
	Bin       string
	Bots      string
	Broken    string
	Window    int
	Executors int
	Slots     int
	Sync      string
	BusyMs    int
	MaxNacks  int
	Seed      int64
	Quiet     bool
	ExtraArgs []string
}

type ackRec struct {
	Line    int    `json:"line"`
	User    string `json:"user,omitempty"`
	UID     string `json:"uid,omitempty"`
	Status  string `json:"status"`
	Raw     bool   `json:"raw,omitempty"`
	E2EUs   int64  `json:"e2e_us"`
	StepUs  int64  `json:"step_us"`
	LatUs   int64  `json:"lat_us"`
	Nacks   int    `json:"nacks"`
	Retries int    `json:"retries"`
	Early   bool   `json:"early,omitempty"`
	T0Us    int64  `json:"t0_us"`
	T1Us    int64  `json:"t1_us"`
}

type exitRec struct {
	TUs      int64  `json:"t_us"`
	Gen      int    `json:"gen"`
	Pid      int    `json:"pid"`
	Signal   string `json:"signal,omitempty"`
	Code     int    `json:"code"`
	Expected bool   `json:"expected"`
}

type crashRec struct {
	At  string `json:"at"`
	On  string `json:"on"`
	Tag string `json:"tag"`
	Pid int    `json:"pid"`
}

type hostProc struct {
	cmd  *exec.Cmd
	pid  int
	gen  int
	done chan struct{}
}

// Runner runs one corpus against fresh host processes on one DB.
type Runner struct {
	o        Opts
	items    []Item
	work     string
	family   string
	db       string
	sock     string
	extSock  string
	crashLog string
	ext      *ExtSim
	client   *http.Client
	ctlCl    *http.Client

	mu        sync.Mutex
	cond      *sync.Cond
	cur       *hostProc
	gen       int
	stopping  bool
	expected  map[int]bool
	armed     []crashRec
	crashSeen int // lines of crash log consumed
	Fired     []crashRec
	Exits     []exitRec
	Restarts  int

	inflight  atomic.Int64
	busy      map[string]int
	Acks      []ackRec
	Abandoned []map[string]any
	Nacks     atomic.Int64
	NackWhy   map[string]int
	clock     int64
	Faults    []map[string]any
	lockWG    sync.WaitGroup
	lockNacks atomic.Int64
	lockOn    atomic.Bool
	Corrupted map[string]string
	LockSpans [][2]int64
	RawLines  []map[string]any
	StopStats []map[string]any
	Elapsed   time.Duration
	hostStderr *os.File
}

func NewRunner(items []Item, work string, o Opts) *Runner {
	r := &Runner{o: o, items: items, work: work, family: "pizza", expected: map[int]bool{}, busy: map[string]int{},
		NackWhy: map[string]int{}, Corrupted: map[string]string{}}
	if len(items) > 0 && items[0].str("ctl") == "init" {
		r.family = items[0].str("bot")
	}
	r.cond = sync.NewCond(&r.mu)
	r.db = filepath.Join(work, "host.sqlite")
	// unix socket paths must be short (108 bytes): use a short dir under /tmp
	short, _ := os.MkdirTemp("", "fg")
	r.sock = filepath.Join(short, "h.sock")
	r.extSock = filepath.Join(short, "e.sock")
	r.crashLog = filepath.Join(work, "crashes.log")
	mk := func(sock string, timeout time.Duration) *http.Client {
		return &http.Client{Timeout: timeout, Transport: &http.Transport{
			DialContext: func(ctx context.Context, _, _ string) (net.Conn, error) {
				var d net.Dialer
				return d.DialContext(ctx, "unix", sock)
			}, MaxIdleConnsPerHost: 64, MaxConnsPerHost: 0}}
	}
	r.client = mk(r.sock, 120*time.Second)
	r.ctlCl = mk(r.sock, 600*time.Second)
	return r
}

func (r *Runner) logf(f string, a ...any) {
	if !r.o.Quiet {
		fmt.Fprintf(os.Stderr, "[%s] "+f+"\n", append([]any{filepath.Base(r.work)}, a...)...)
	}
}

// ------------------------------------------------------------------ host process

func (r *Runner) hostArgs() []string {
	var arm []string
	for _, c := range r.armed {
		if c.On != "" {
			arm = append(arm, c.At+":"+c.On)
		} else {
			arm = append(arm, c.At)
		}
	}
	args := []string{"-db", r.db, "-sock", r.sock, "-ext", r.extSock, "-bots", r.o.Bots, "-family", r.family,
		"-crashlog", r.crashLog, "-executors", fmt.Sprint(r.o.Executors), "-sync", r.o.Sync,
		"-busy-ms", fmt.Sprint(r.o.BusyMs), "-cgo-slots", fmt.Sprint(r.o.Slots)}
	if len(arm) > 0 {
		args = append(args, "-arm", strings.Join(arm, ","))
	}
	if r.o.Broken != "" {
		args = append(args, "-broken", r.o.Broken)
	}
	return append(args, r.o.ExtraArgs...)
}

// startHost starts a host process and waits for READY. Caller holds no lock.
func (r *Runner) startHost() error {
	for attempt := 0; attempt < 20; attempt++ {
		cmd := exec.Command(r.o.Bin, r.hostArgs()...)
		cmd.Stderr = r.hostStderr
		out, err := cmd.StdoutPipe()
		if err != nil {
			return err
		}
		if err := cmd.Start(); err != nil {
			return err
		}
		rd := bufio.NewReader(out)
		lineCh := make(chan string, 1)
		go func() {
			l, _ := rd.ReadString('\n')
			lineCh <- l
			io.Copy(io.Discard, rd)
		}()
		var line string
		select {
		case line = <-lineCh:
		case <-time.After(120 * time.Second):
			cmd.Process.Kill()
		}
		if !strings.HasPrefix(line, "READY") {
			cmd.Wait()
			r.mu.Lock()
			r.Exits = append(r.Exits, exitRec{Gen: -1, Pid: cmd.Process.Pid, Code: cmd.ProcessState.ExitCode(), Expected: true})
			r.mu.Unlock()
			time.Sleep(500 * time.Millisecond)
			continue
		}
		r.mu.Lock()
		r.gen++
		p := &hostProc{cmd: cmd, pid: cmd.Process.Pid, gen: r.gen, done: make(chan struct{})}
		r.cur = p
		r.cond.Broadcast()
		r.mu.Unlock()
		go r.supervise(p)
		return nil
	}
	return errors.New("host did not start (see host.stderr)")
}

func (r *Runner) readCrashLog() {
	b, err := os.ReadFile(r.crashLog)
	if err != nil {
		return
	}
	lines := strings.Split(strings.TrimSpace(string(b)), "\n")
	for i := r.crashSeen; i < len(lines); i++ {
		if lines[i] == "" {
			continue
		}
		var c crashRec
		if json.Unmarshal([]byte(lines[i]), &c) == nil {
			r.Fired = append(r.Fired, c)
			for j, a := range r.armed {
				if a.At == c.At && a.On == c.On {
					r.armed = append(r.armed[:j], r.armed[j+1:]...)
					break
				}
			}
		}
	}
	r.crashSeen = len(lines)
}

// supervise waits for the host process; an unexpected death (kill -9 at an armed
// point) restarts the host from the same DB. Unacknowledged updates are retried by
// their delivery goroutines against the new process.
func (r *Runner) supervise(p *hostProc) {
	err := p.cmd.Wait()
	close(p.done)
	rec := exitRec{Gen: p.gen, Pid: p.pid, TUs: time.Since(epoch).Microseconds()}
	var ee *exec.ExitError
	if errors.As(err, &ee) {
		if ws, ok := ee.Sys().(syscall.WaitStatus); ok && ws.Signaled() {
			rec.Signal = ws.Signal().String()
		}
		rec.Code = ee.ExitCode()
	}
	r.mu.Lock()
	rec.Expected = r.expected[p.gen]
	r.Exits = append(r.Exits, rec)
	if r.cur == p {
		r.cur = nil
	}
	restart := !rec.Expected && !r.stopping
	if restart {
		r.readCrashLog()
		r.Restarts++
	}
	r.cond.Broadcast()
	r.mu.Unlock()
	if restart {
		r.logf("host gen %d died (%s), restarting", p.gen, rec.Signal)
		if err := r.startHost(); err != nil {
			r.logf("restart failed: %v", err)
		}
	}
}

func (r *Runner) waitAlive() *hostProc {
	r.mu.Lock()
	defer r.mu.Unlock()
	for r.cur == nil {
		r.cond.Wait()
	}
	return r.cur
}

func (r *Runner) stopHost() (map[string]any, error) {
	for attempt := 0; attempt < 10; attempt++ {
		r.mu.Lock()
		p := r.cur
		r.stopping = true
		if p != nil {
			r.expected[p.gen] = true
		}
		r.mu.Unlock()
		if p == nil {
			r.mu.Lock()
			r.stopping = false
			r.mu.Unlock()
			return nil, errors.New("no host")
		}
		st, err := r.postJSON(r.ctlCl, "/ctl", map[string]any{"ctl": "stop"})
		select {
		case <-p.done:
		case <-time.After(60 * time.Second):
			p.cmd.Process.Kill()
			<-p.done
		}
		r.mu.Lock()
		r.stopping = false
		killed := false
		for _, e := range r.Exits {
			if e.Gen == p.gen && e.Signal == "killed" {
				killed = true
			}
		}
		if killed { // an armed crash point fired during the final drain: restart and stop again
			r.readCrashLog()
			r.Restarts++
		}
		r.mu.Unlock()
		if !killed {
			return st, err
		}
		if err := r.startHost(); err != nil {
			return nil, err
		}
	}
	return nil, errors.New("host could not be stopped")
}

func (r *Runner) postJSON(cl *http.Client, path string, body any) (map[string]any, error) {
	b, _ := json.Marshal(body)
	code, rb, err := r.post(cl, path, b)
	if err != nil {
		return nil, err
	}
	var out map[string]any
	json.Unmarshal(rb, &out)
	if code != 200 {
		return out, fmt.Errorf("%s -> %d %s", path, code, string(rb))
	}
	return out, nil
}

func (r *Runner) post(cl *http.Client, path string, body []byte) (int, []byte, error) {
	resp, err := cl.Post("http://host"+path, "application/json", bytes.NewReader(body))
	if err != nil {
		return 0, nil, err
	}
	defer resp.Body.Close()
	rb, err := io.ReadAll(resp.Body)
	if err != nil {
		return 0, nil, err
	}
	return resp.StatusCode, rb, nil
}

// ctl sends a control command; if the host dies while executing it (an armed crash
// point in the executor during a barrier), wait for the restart and re-send.
func (r *Runner) ctl(cmd map[string]any) (map[string]any, error) {
	for attempt := 0; attempt < 50; attempt++ {
		p := r.waitAlive()
		out, err := r.postJSON(r.ctlCl, "/ctl", cmd)
		if err == nil {
			return out, nil
		}
		select {
		case <-p.done:
			continue // died; supervisor restarts it
		case <-time.After(200 * time.Millisecond):
		}
		if out != nil && out["error"] != nil {
			// a real error from a live host (e.g. barrier blocked by an injected DB lock): retry a few times
			r.logf("ctl %v error: %v", cmd["ctl"], out["error"])
			time.Sleep(500 * time.Millisecond)
			continue
		}
	}
	return nil, fmt.Errorf("ctl %v failed repeatedly", cmd)
}

// ------------------------------------------------------------------ delivery

type delivery struct {
	line  int
	user  string
	uid   string
	body  []byte
	raw   bool
	start time.Time
}

func (r *Runner) deliver(d *delivery, wg *sync.WaitGroup) {
	defer wg.Done()
	defer func() {
		r.inflight.Add(-1)
		r.mu.Lock()
		r.cond.Broadcast()
		r.mu.Unlock()
	}()
	nacks, retries := 0, 0
	for {
		p := r.waitAlive()
		code, rb, err := r.post(r.client, "/update", d.body)
		if err != nil {
			retries++
			select {
			case <-p.done:
			case <-time.After(30 * time.Millisecond):
			}
			continue
		}
		var resp map[string]any
		json.Unmarshal(rb, &resp)
		if code == 200 {
			st, _ := resp["status"].(string)
			stepUs, _ := resp["step_us"].(float64)
			latUs, _ := resp["lat_us"].(float64)
			early, _ := resp["early"].(bool)
			r.mu.Lock()
			r.Acks = append(r.Acks, ackRec{Line: d.line, User: d.user, UID: d.uid, Status: st, Raw: d.raw,
				E2EUs: time.Since(d.start).Microseconds(), StepUs: int64(stepUs), LatUs: int64(latUs), Nacks: nacks,
				Retries: retries, Early: early, T0Us: d.start.Sub(epoch).Microseconds(), T1Us: time.Since(epoch).Microseconds()})
			if d.user != "" {
				r.busy[d.user]--
				r.cond.Broadcast()
			}
			r.mu.Unlock()
			return
		}
		nacks++
		r.Nacks.Add(1)
		if r.lockOn.Load() {
			r.lockNacks.Add(1)
		}
		why, _ := resp["why"].(string)
		r.mu.Lock()
		r.NackWhy[trunc(why, 60)]++
		r.mu.Unlock()
		if nacks > r.o.MaxNacks {
			r.mu.Lock()
			r.Abandoned = append(r.Abandoned, map[string]any{"line": d.line, "user": d.user, "uid": d.uid, "why": why})
			if d.user != "" {
				r.busy[d.user]--
				r.cond.Broadcast()
			}
			r.mu.Unlock()
			return
		}
		time.Sleep(30 * time.Millisecond)
	}
}

func trunc(s string, n int) string {
	if len(s) > n {
		return s[:n]
	}
	return s
}

func (r *Runner) quiesceDeliveries(wg *sync.WaitGroup) { wg.Wait() }

// ------------------------------------------------------------------ faults by the outside world

func (r *Runner) dbLock(ms int64) {
	ready := make(chan struct{})
	r.lockWG.Add(1)
	go func() {
		defer r.lockWG.Done()
		db, err := sql.Open("sqlite", "file:"+r.db+"?_pragma=busy_timeout(30000)")
		if err != nil {
			close(ready)
			return
		}
		defer db.Close()
		conn, err := db.Conn(context.Background())
		if err != nil {
			close(ready)
			return
		}
		defer conn.Close()
		if _, err := conn.ExecContext(context.Background(), "BEGIN EXCLUSIVE"); err != nil {
			r.logf("db_lock: %v", err)
			close(ready)
			return
		}
		r.lockOn.Store(true)
		t1 := time.Since(epoch).Microseconds()
		close(ready)
		time.Sleep(time.Duration(ms) * time.Millisecond)
		r.lockOn.Store(false)
		_, cerr := conn.ExecContext(context.Background(), "COMMIT")
		r.mu.Lock()
		r.LockSpans = append(r.LockSpans, [2]int64{t1, time.Since(epoch).Microseconds()})
		r.mu.Unlock()
		if cerr != nil {
			r.logf("db_lock commit: %v", cerr)
		}
	}()
	<-ready
}

func (r *Runner) corruptSnapshot(user, mode string) error {
	db, err := sql.Open("sqlite", "file:"+r.db+"?_pragma=busy_timeout(30000)")
	if err != nil {
		return err
	}
	defer db.Close()
	var snap sql.NullString
	var hw int64
	if err := db.QueryRow(`SELECT snapshot, hw_seq FROM users WHERE user=?`, user).Scan(&snap, &hw); err != nil {
		return nil // unknown user: nothing to corrupt
	}
	var s map[string]any
	json.Unmarshal([]byte(snap.String), &s)
	with := func(k string, v any) string {
		m := map[string]any{}
		for a, b := range s {
			m[a] = b
		}
		if v == nil {
			delete(m, k)
		} else {
			m[k] = v
		}
		b, _ := json.Marshal(m)
		return string(b)
	}
	var txt any
	switch mode {
	case "garbage":
		txt = snap.String[:len(snap.String)/2]
	case "not_object":
		txt = "[1,2,3]"
	case "unknown_field":
		txt = with("evil", 1)
	case "bad_version":
		txt = with("v", 2)
	case "wrong_type":
		txt = with("seq", fmt.Sprint(hw))
	case "missing_at":
		txt = with("at", nil)
	case "huge_seq":
		txt = with("seq", int64(maxInt53))
	case "rollback": // an older valid snapshot of this user (restored from an old backup)
		var old sql.NullString
		db.QueryRow(`SELECT snapshot FROM transitions WHERE user=? AND changed=1 AND seq<? AND snapshot IS NOT NULL
			ORDER BY n DESC LIMIT 1 OFFSET 1`, user, hw).Scan(&old)
		if old.Valid {
			txt = old.String
		} else {
			txt = with("seq", hw-1)
		}
	case "deleted_node":
		txt = with("at", "no_such_message")
	case "null":
		txt = nil
	default:
		return fmt.Errorf("unknown corrupt mode %s", mode)
	}
	_, err = db.Exec(`UPDATE users SET snapshot=? WHERE user=?`, txt, user)
	r.Corrupted[user] = mode
	return err
}

// ------------------------------------------------------------------ main loop

func (r *Runner) Run() error {
	os.MkdirAll(r.work, 0o755)
	for _, f := range []string{"host.sqlite", "host.sqlite-wal", "host.sqlite-shm", "crashes.log"} {
		os.Remove(filepath.Join(r.work, f))
	}
	var err error
	r.hostStderr, err = os.OpenFile(filepath.Join(r.work, "host.stderr"), os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0o644)
	if err != nil {
		return err
	}
	defer r.hostStderr.Close()
	r.ext = NewExtSim(r.o.Seed)
	if err := r.ext.Serve(r.extSock); err != nil {
		return err
	}
	defer r.ext.Close()
	t0 := time.Now()
	if err := r.startHost(); err != nil {
		return err
	}
	var wg sync.WaitGroup
	for _, it := range r.items {
		if it.str("ctl") == "init" {
			continue
		}
		if it.has("ctl") {
			wg.Wait()
			if err := r.doCtl(it); err != nil {
				return fmt.Errorf("line %d: %w", it.Line, err)
			}
			continue
		}
		if it.has("raw") || it.has("raw_text") {
			r.waitWindow()
			var body []byte
			if it.has("raw_text") {
				body = []byte(it.str("raw_text"))
			} else {
				body, _ = json.Marshal(it.Raw["raw"])
			}
			r.RawLines = append(r.RawLines, map[string]any{"line": it.Line, "expect": it.str("expect")})
			r.inflight.Add(1)
			wg.Add(1)
			go r.deliver(&delivery{line: it.Line, body: body, raw: true, start: time.Now()}, &wg)
			continue
		}
		user := it.str("user")
		r.mu.Lock()
		for r.busy[user] > 0 || r.inflight.Load() >= int64(r.o.Window) {
			r.cond.Wait()
		}
		r.mu.Unlock()
		if d := it.num("delay_ms"); d > 0 {
			time.Sleep(time.Duration(d) * time.Millisecond)
		}
		body := map[string]any{}
		for _, k := range []string{"user", "update_id", "event", "arg"} {
			if v, ok := it.Raw[k]; ok {
				body[k] = v
			}
		}
		bb, _ := json.Marshal(body)
		n := int(it.num("dup"))
		if n < 1 {
			n = 1
		}
		r.mu.Lock()
		r.busy[user] += n
		r.mu.Unlock()
		uid := fmt.Sprintf("tg:%d", it.num("update_id"))
		for k := 0; k < n; k++ {
			r.inflight.Add(1)
			wg.Add(1)
			go r.deliver(&delivery{line: it.Line, user: user, uid: uid, body: bb, start: time.Now()}, &wg)
		}
	}
	wg.Wait()
	if _, err := r.ctl(map[string]any{"ctl": "barrier"}); err != nil {
		return err
	}
	r.ctl(map[string]any{"ctl": "executor", "mode": "resume"})
	if _, err := r.ctl(map[string]any{"ctl": "barrier"}); err != nil {
		return err
	}
	r.lockWG.Wait()
	if _, err := r.ctl(map[string]any{"ctl": "barrier"}); err != nil {
		return err
	}
	r.Elapsed = time.Since(t0)
	st, err := r.stopHost()
	if err != nil {
		return err
	}
	r.StopStats = append(r.StopStats, st)
	return nil
}

func (r *Runner) waitWindow() {
	r.mu.Lock()
	for r.inflight.Load() >= int64(r.o.Window) {
		r.cond.Wait()
	}
	r.mu.Unlock()
}

func (r *Runner) doCtl(it Item) error {
	k := it.str("ctl")
	if _, err := r.ctl(map[string]any{"ctl": "barrier"}); err != nil {
		return err
	}
	switch k {
	case "crash":
		c := crashRec{At: it.str("at"), On: it.str("on")}
		r.mu.Lock()
		r.armed = append(r.armed, c)
		r.mu.Unlock()
		r.Faults = append(r.Faults, map[string]any{"line": it.Line, "fault": "crash", "at": c.At, "on": c.On})
		_, err := r.ctl(map[string]any{"ctl": "crash", "at": c.At, "on": c.On})
		return err
	case "restart_host":
		st, err := r.stopHost()
		if err != nil {
			return err
		}
		r.StopStats = append(r.StopStats, st)
		r.mu.Lock()
		r.Restarts++
		r.mu.Unlock()
		return r.startHost()
	case "upgrade":
		_, err := r.ctl(map[string]any{"ctl": "upgrade", "bot": it.str("bot")})
		return err
	case "advance_ms":
		r.clock += it.num("ms")
		_, err := r.ctl(map[string]any{"ctl": "advance_to", "t": r.clock})
		return err
	case "external":
		tg := it.str("target")
		if tg == "" {
			tg = "all"
		}
		cnt := int(it.num("count"))
		if cnt == 0 {
			cnt = 1
		}
		r.ext.SetMode(tg, it.str("mode"), cnt, int(it.num("ms")), it.Line)
		r.Faults = append(r.Faults, map[string]any{"line": it.Line, "fault": "external", "target": tg, "mode": it.str("mode"), "count": cnt})
	case "executor":
		_, err := r.ctl(map[string]any{"ctl": "executor", "mode": it.str("mode")})
		return err
	case "timer_mode":
		c := map[string]any{"ctl": "timer_mode"}
		for _, x := range []string{"dup", "ignore_cancel"} {
			if v, ok := it.Raw[x]; ok {
				c[x] = v
			}
		}
		_, err := r.ctl(c)
		return err
	case "replay_stale":
		_, err := r.ctl(map[string]any{"ctl": "replay_stale", "user": it.str("user")})
		return err
	case "reset_user":
		_, err := r.ctl(map[string]any{"ctl": "reset_user", "user": it.str("user"), "id": fmt.Sprint(it.Line)})
		return err
	case "db_lock":
		r.dbLock(it.num("ms"))
		r.Faults = append(r.Faults, map[string]any{"line": it.Line, "fault": "db_lock", "ms": it.num("ms")})
	case "engine_fault":
		r.Faults = append(r.Faults, map[string]any{"line": it.Line, "fault": "engine_fault", "mode": it.str("mode")})
		_, err := r.ctl(map[string]any{"ctl": "engine_fault", "mode": it.str("mode")})
		return err
	case "corrupt_snapshot":
		return r.corruptSnapshot(it.str("user"), it.str("mode"))
	case "barrier", "note":
	default:
		return fmt.Errorf("unknown ctl %s", k)
	}
	return nil
}

// RestartCheck: a fresh host process on the same DB must not change anything.
func (r *Runner) RestartCheck() error {
	if err := r.startHost(); err != nil {
		return err
	}
	if _, err := r.ctl(map[string]any{"ctl": "barrier"}); err != nil {
		return err
	}
	st, err := r.stopHost()
	r.StopStats = append(r.StopStats, st)
	return err
}

func sortedKeys[M ~map[string]V, V any](m M) []string {
	out := make([]string, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}

package host

import (
	"database/sql"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"syscall"
	"time"
)

// Config of a host process.
type Config struct {
	DB            string
	Bots          string
	Family        string // bot family of new users
	ExtSock       string // unix socket of the simulated CRM/Telegram
	CrashLog      string // where an armed crash point records itself before kill -9
	Executors     int
	BusyMs        int
	Sync          string
	EngineTimeout time.Duration
	ExtTimeout    time.Duration
	Broken        map[string]bool
	Armed         []CrashPoint
}

// BrokenVariants: deliberately broken host variants for the RED matrix (each is a
// real fault in the host code path, the checker is unchanged).
var BrokenVariants = map[string]string{
	"no-dedup":               "update_id dedup disabled (pre-check and in-transaction check)",
	"ack-before-commit":      "HTTP 200 (ack) is flushed before the step transaction commits",
	"outbox-after-ack":       "snapshot+processed committed and acked, outbox rows in a later transaction",
	"const-guards":           "every guard is constant true",
	"no-drain":               "guards computed without draining the user's pending calls (no rule G1)",
	"no-resource-lock":       "slot_free without slot lock, counting executed reservations only (no rule G2)",
	"seq-keys-start":         "reset via start (seq restarts at 1) + old outbox history deleted (ET-01 path)",
	"random-keys":            "external idempotency key gets a fresh nonce on every attempt",
	"no-user-lock":           "no per-user mutex and no optimistic version check",
	"stale-token-lookup":     "timer token taken from the current snapshot instead of the fired timer",
	"no-validate":            "Telegram updates not validated (timer:* and token pass)",
	"memory-timers":          "scheduler state not durable: timers lost on restart",
	"trust-snapshot":         "stored snapshot passed to the engine as-is (no schema/high-water check, no repair)",
	"no-capacity-check":      "reserve_slot trusts the guard (no capacity check in the domain write)",
	"no-ack-retry":           "DB error after a successful external effect re-executes the effect later",
	"no-crash-points":        "crash ctl accepted but never armed (test of the test)",
	"skip-external":          "external calls are marked done without calling the external service",
	"no-reply-validate":      "engine replies are trusted (no id/schema/doc checks)",
	"unload-no-wait":         "document reload frees the old automaton without waiting for in-flight calls",
	"strict-snapshot-timers": "reply validator also requires snapshot.timers to be timers of the current document (first version of this host)",
}

// CrashPoint is an armed kill -9 point.
type CrashPoint struct {
	At string `json:"at"`
	On string `json:"on,omitempty"`
}

// Host is the production-like host.
type Host struct {
	cfg     Config
	db      *sql.DB
	eng     *Engines
	faults  *Faults
	ext     *ExtClient
	broken  map[string]bool
	clockMu sync.Mutex
	clock   int64

	locksMu sync.Mutex
	ulocks  map[string]*sync.Mutex // per-user step lock
	olocks  map[string]*sync.Mutex // per-user outbox lock (executor + G1 drain)
	rlocks  map[string]*sync.Mutex // shared resources (G2)

	crashMu sync.Mutex
	armed   []CrashPoint

	writeMu    sync.Mutex
	inflight   sync.WaitGroup
	inflightN  atomic.Int64
	exec       *Executor
	stats      sync.Map // name -> *atomic.Int64
	famDocMu   sync.Mutex
	famDoc     map[string]string
	timerDup   atomic.Int64
	ignoreCanc atomic.Bool
}

func (h *Host) stat(k string, n int64) {
	v, _ := h.stats.LoadOrStore(k, new(atomic.Int64))
	v.(*atomic.Int64).Add(n)
}

// Stats returns the counters.
func (h *Host) Stats() map[string]int64 {
	out := map[string]int64{}
	h.stats.Range(func(k, v any) bool { out[k.(string)] = v.(*atomic.Int64).Load(); return true })
	return out
}

// New opens the DB, seeds it and starts the executor.
func New(cfg Config) (*Host, error) {
	if cfg.Executors <= 0 {
		cfg.Executors = 4
	}
	db, err := OpenDB(cfg.DB, cfg.BusyMs, cfg.Sync, 24)
	if err != nil {
		return nil, err
	}
	var initErr error
	for i := 0; i < 50; i++ { // the DB may be locked by an injected db_lock right now
		if initErr = initSchema(db); initErr == nil || !IsBusy(initErr) {
			break
		}
		time.Sleep(100 * time.Millisecond)
	}
	if initErr != nil {
		return nil, initErr
	}
	f := NewFaults()
	h := &Host{cfg: cfg, db: db, faults: f, broken: cfg.Broken,
		ulocks: map[string]*sync.Mutex{}, olocks: map[string]*sync.Mutex{}, rlocks: map[string]*sync.Mutex{},
		famDoc: map[string]string{}}
	if h.broken == nil {
		h.broken = map[string]bool{}
	}
	h.eng = NewEngines(cfg.Bots, cfg.EngineTimeout, f)
	h.eng.Validate = !h.broken["no-reply-validate"]
	h.eng.UnsafeDrop = h.broken["unload-no-wait"]
	h.eng.StrictTimers = h.broken["strict-snapshot-timers"]
	f.OnKill = func() { h.die(CrashPoint{At: "engine_kill"}, "engine") }
	f.OnEvent = func(kind, mode string) { h.log(kind, "", map[string]any{"mode": mode}) }
	h.ext = NewExtClient(cfg.ExtSock, cfg.ExtTimeout)
	if v, ok := getMeta(db, "clock_ms"); ok {
		h.clock, _ = strconv.ParseInt(v, 10, 64)
	}
	if v, ok := getMeta(db, "timer_dup"); ok {
		n, _ := strconv.ParseInt(v, 10, 64)
		h.timerDup.Store(n)
	}
	if v, ok := getMeta(db, "ignore_cancel"); ok {
		h.ignoreCanc.Store(v == "1")
	}
	if !h.broken["no-crash-points"] {
		h.armed = append(h.armed, cfg.Armed...)
	}
	if h.broken["memory-timers"] {
		// BROKEN: the scheduler kept its state in memory only -> lost on restart
		db.Exec(`UPDATE timers SET state='lost', delivered=1 WHERE delivered=0`) // before any writer exists
	}
	h.exec = newExecutor(h, cfg.Executors)
	return h, nil
}

// Close stops the executor and unloads the engines.
func (h *Host) Close() {
	h.exec.stop()
	h.eng.CloseAll()
	h.db.Close()
}

func (h *Host) lockOf(m map[string]*sync.Mutex, k string) *sync.Mutex {
	h.locksMu.Lock()
	defer h.locksMu.Unlock()
	l := m[k]
	if l == nil {
		l = &sync.Mutex{}
		m[k] = l
	}
	return l
}

func (h *Host) now() int64 {
	h.clockMu.Lock()
	defer h.clockMu.Unlock()
	return h.clock
}

// crashPoint: a REAL kill -9 of this host process at an armed point.
func (h *Host) crashPoint(at, tag string) {
	h.crashMu.Lock()
	for i, p := range h.armed {
		if p.At == at && (p.On == "" || p.On == tag) {
			h.armed = append(h.armed[:i], h.armed[i+1:]...)
			h.crashMu.Unlock()
			h.die(p, tag)
			return
		}
	}
	h.crashMu.Unlock()
}

func (h *Host) die(p CrashPoint, tag string) {
	if h.cfg.CrashLog != "" {
		if f, err := os.OpenFile(h.cfg.CrashLog, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o644); err == nil {
			b, _ := json.Marshal(map[string]any{"at": p.At, "on": p.On, "tag": tag, "pid": os.Getpid()})
			f.Write(append(b, '\n'))
			f.Sync()
			f.Close()
		}
	}
	fmt.Fprintf(os.Stderr, "CRASH at=%s on=%s tag=%s pid=%d\n", p.At, p.On, tag, os.Getpid())
	syscall.Kill(os.Getpid(), syscall.SIGKILL)
	select {} // never returns
}

// Arm adds a crash point (ctl crash).
func (h *Host) Arm(p CrashPoint) {
	if h.broken["no-crash-points"] {
		return
	}
	h.crashMu.Lock()
	h.armed = append(h.armed, p)
	h.crashMu.Unlock()
}

func (h *Host) log(kind, user string, detail any) {
	b, _ := json.Marshal(detail)
	h.execW(`INSERT INTO host_log(kind,user,detail) VALUES(?,?,?)`, kind, user, string(b))
}

// FamilyDoc returns the current document of a family (persisted in meta doc:<family>).
func (h *Host) FamilyDoc(family string) string {
	h.famDocMu.Lock()
	defer h.famDocMu.Unlock()
	if d, ok := h.famDoc[family]; ok {
		return d
	}
	d := family
	if v, ok := getMeta(h.db, "doc:"+family); ok {
		d = v
	}
	h.famDoc[family] = d
	return d
}

func familyOf(doc string) string { return strings.SplitN(doc, ".", 2)[0] }

// DocTimerNames lists the timer names of a document file.
func (h *Host) DocTimerNames(doc string) ([]string, error) {
	b, err := os.ReadFile(filepath.Join(h.cfg.Bots, doc+".bot.json"))
	if err != nil {
		return nil, err
	}
	di, err := parseDocInfo(doc, b)
	if err != nil {
		return nil, err
	}
	n := di.TimerNames()
	sort.Strings(n)
	return n, nil
}

// Clock returns the simulated clock (ms).
func (h *Host) Clock() int64 { return h.now() }

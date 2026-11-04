package main

// The production-like host (docs/host-contract.md, sections 1–4):
//   - ingest: an update is written to the inbound queue before it is acknowledged;
//   - one step worker per user (strict per-user order), duplicate filtering by update id,
//     the per-user barrier (sequential outbox actions of the user executed before a step),
//     the step, one transaction: snapshot + processed mark + outbox rows + step log;
//   - a sequential executor per user (order by seq, position) and a separate pool for
//     background actions; idempotency keys user:update_id:action:occurrence, run ids for calls
//     and timers, outcome events recorded in the transaction that marks the call executed,
//     reconciliation by key before `failed`;
//   - a scheduler of timer events; reset (`restart`) and loss of a snapshot (high-water mark).
// Crash injection: crashPoint(name) abandons the whole instance; a new instance recovers from
// the store.

import (
	"context"
	"crypto/rand"
	"database/sql"
	"encoding/hex"
	"encoding/json"
	"fmt"
	mrand "math/rand"
	"strings"
	"sync"
	"sync/atomic"
	"time"
)

type Config struct {
	noIdem, noRecon, noBarrier, inlineBG, noLock, outcomeSeparate, noDedup bool
	crashProb                                                           float64
	crashPoints                                                         map[string]bool // nil: all
	timeScale                                                           float64         // real ms per bot ms
	unloadDelay                                                         time.Duration
	bgWorkers                                                           int
	dupProb                                                             float64
	execDelay                                                           time.Duration // slow sequential executor (makes a missing barrier visible)
	seed                                                                int64
	noSteplog                                                           bool
}

var crashPointNames = []string{
	"ingest_before_commit", "ingest_after_commit",
	"step_before_commit", "step_after_commit",
	"exec_before_external", "exec_after_external", "exec_between_mark_and_outcome", "exec_after_record",
	"bg_before_external", "bg_after_external", "bg_after_record",
	"sched_before_enqueue", "sched_after_enqueue",
	"reload_after_load",
}

type userState struct {
	mu         sync.Mutex
	stepActive bool
	stepKick   bool
	execActive bool
	execKick   bool
	seqPending int
	barrier    chan struct{}
}

type Instance struct {
	gen    int
	cfg    *Config
	store  *Store
	reg    *Registry
	sims   *Sims
	ctx    context.Context
	cancel context.CancelFunc
	wg     sync.WaitGroup
	isDead atomic.Bool
	died   chan struct{}
	once   sync.Once
	rngMu  sync.Mutex
	rng    *mrand.Rand

	umu   sync.Mutex
	users map[string]*userState
	bgq   chan int64
	bgSet sync.Map // outbox ids queued

	crashes  *sync.Map // point → count (shared across instances)
	stats    *Stats
}

type Stats struct {
	steps, drops, dups, engineErrors, recons, reconHits, failed, bgFail, seqFail atomic.Int64
	barrierWaitMaxUs, barrierWaitTotalUs                                         atomic.Int64
	stepLatMu                                                                    sync.Mutex
	stepLat                                                                      []int64 // µs from inbound insert to commit
}

func newID() string {
	var b [12]byte
	rand.Read(b[:])
	return hex.EncodeToString(b[:])
}

func nowMs() int64 { return time.Now().UnixMicro() / 1000 }

func (in *Instance) dead() bool { return in.isDead.Load() }

func (in *Instance) crash(point string) {
	in.once.Do(func() {
		in.isDead.Store(true)
		in.cancel()
		if in.crashes != nil {
			v, _ := in.crashes.LoadOrStore(point, new(atomic.Int64))
			v.(*atomic.Int64).Add(1)
		}
		close(in.died)
	})
}

func (in *Instance) crashPoint(point string) error {
	if in.dead() {
		return errCrashed
	}
	if in.cfg.crashProb <= 0 || (in.cfg.crashPoints != nil && !in.cfg.crashPoints[point]) {
		return nil
	}
	in.rngMu.Lock()
	hit := in.rng.Float64() < in.cfg.crashProb
	in.rngMu.Unlock()
	if hit {
		in.crash(point)
		return errCrashed
	}
	return nil
}

func (in *Instance) rand() float64 {
	in.rngMu.Lock()
	defer in.rngMu.Unlock()
	return in.rng.Float64()
}

func (in *Instance) us(u string) *userState {
	in.umu.Lock()
	defer in.umu.Unlock()
	s := in.users[u]
	if s == nil {
		s = &userState{barrier: make(chan struct{})}
		close(s.barrier)
		in.users[u] = s
	}
	return s
}

func (in *Instance) addPending(u string, n int) {
	if n == 0 {
		return
	}
	s := in.us(u)
	s.mu.Lock()
	was := s.seqPending
	s.seqPending += n
	if was == 0 && s.seqPending > 0 {
		s.barrier = make(chan struct{})
	} else if was > 0 && s.seqPending == 0 {
		close(s.barrier)
	}
	if s.seqPending < 0 {
		panic("negative pending for " + u)
	}
	s.mu.Unlock()
}

// go runs a goroutine of the instance; errCrashed ends it quietly.
func (in *Instance) goRun(f func()) {
	in.wg.Add(1)
	go func() {
		defer in.wg.Done()
		f()
	}()
}

// ---------------- recovery ----------------

func startInstance(gen int, cfg *Config, st *Store, sims *Sims, crashes *sync.Map, stats *Stats, bots []string) (*Instance, error) {
	ctx, cancel := context.WithCancel(context.Background())
	in := &Instance{gen: gen, cfg: cfg, store: st, reg: &Registry{}, sims: sims, ctx: ctx, cancel: cancel,
		died: make(chan struct{}), rng: mrand.New(mrand.NewSource(cfg.seed*1000 + int64(gen))),
		users: map[string]*userState{}, bgq: make(chan int64, 1<<20), crashes: crashes, stats: stats}
	// documents: the version recorded in the store (a reload is persisted)
	for _, b := range bots {
		key := b + "@1"
		var v string
		if err := st.w.QueryRow(`SELECT ver FROM docs WHERE name=?`, b).Scan(&v); err == nil {
			key = v
		}
		dv, err := loadDoc(key)
		if err != nil {
			return nil, err
		}
		in.reg.ptr(b).Store(dv)
	}
	// barrier counters from the outbox
	q := `SELECT user, count(*) FROM outbox WHERE state<>1 AND detached=0 GROUP BY user`
	if cfg.inlineBG {
		q = `SELECT user, count(*) FROM outbox WHERE state<>1 GROUP BY user`
	}
	rows, err := st.w.Query(q)
	if err != nil {
		return nil, err
	}
	pend := map[string]int{}
	for rows.Next() {
		var u string
		var n int
		rows.Scan(&u, &n)
		pend[u] = n
	}
	rows.Close()
	for u, n := range pend {
		in.addPending(u, n)
	}
	// work to resume
	var inUsers, exUsers []string
	rows, _ = st.w.Query(`SELECT DISTINCT user FROM inbound`)
	for rows.Next() {
		var u string
		rows.Scan(&u)
		inUsers = append(inUsers, u)
	}
	rows.Close()
	rows, _ = st.w.Query(`SELECT DISTINCT user FROM outbox WHERE state=0`)
	for rows.Next() {
		var u string
		rows.Scan(&u)
		exUsers = append(exUsers, u)
	}
	rows.Close()
	var bgIDs []int64
	if !cfg.inlineBG {
		rows, _ = st.w.Query(`SELECT id FROM outbox WHERE state=0 AND detached=1 ORDER BY id`)
		for rows.Next() {
			var id int64
			rows.Scan(&id)
			bgIDs = append(bgIDs, id)
		}
		rows.Close()
	}
	for i := 0; i < cfg.bgWorkers; i++ {
		in.goRun(in.bgWorker)
	}
	in.goRun(in.scheduler)
	for _, id := range bgIDs {
		in.enqueueBG(id)
	}
	for _, u := range exUsers {
		in.kickExec(u)
	}
	for _, u := range inUsers {
		in.kickStep(u)
	}
	return in, nil
}

// ---------------- ingest ----------------

type Update struct {
	User  string
	ID    int64
	Event string
	Data  map[string]any
}

// Ingest stores an update in the inbound queue; nil means acknowledged.
func (in *Instance) Ingest(up Update) error {
	if err := in.crashPoint("ingest_before_commit"); err != nil {
		return err
	}
	uid := fmt.Sprintf("tg:%d", up.ID)
	err := in.store.tx(in, func(t txLike) error {
		if _, err := t.Exec(`INSERT OR IGNORE INTO users(user, bot, snap, ver, hwm) VALUES(?, ?, NULL, 0, 0)`, up.User, botOf(up.User)); err != nil {
			return err
		}
		if _, err := t.Exec(`INSERT INTO inbound(user, uid, kind, event, data, t) VALUES(?, ?, 'tg', ?, ?, ?)`,
			up.User, uid, up.Event, mustJSON(up.Data), time.Now().UnixMicro()); err != nil {
			return err
		}
		_, err := t.Exec(`INSERT INTO ingest_log(user, uid, event) VALUES(?, ?, ?)`, up.User, uid, up.Event)
		return err
	})
	if err != nil {
		return err
	}
	if err := in.crashPoint("ingest_after_commit"); err != nil {
		return err
	}
	in.kickStep(up.User)
	return nil
}

// ---------------- step worker ----------------

func (in *Instance) kickStep(u string) {
	if in.dead() {
		return
	}
	s := in.us(u)
	s.mu.Lock()
	if s.stepActive && !in.cfg.noLock {
		s.stepKick = true
		s.mu.Unlock()
		return
	}
	s.stepActive = true
	s.mu.Unlock()
	in.goRun(func() { in.stepLoop(u) })
}

type inRow struct {
	id    int64
	uid   string
	kind  string
	event string
	token sql.NullInt64
	run   sql.NullString
	data  string
	t     int64
}

func (in *Instance) stepLoop(u string) {
	s := in.us(u)
	for {
		if in.dead() {
			return
		}
		var r inRow
		q := `SELECT id, uid, kind, event, token, run, data, t FROM inbound WHERE user=? ORDER BY id LIMIT 1`
		if in.cfg.noLock { // control: several workers per user, each takes any row
			q = `SELECT id, uid, kind, event, token, run, data, t FROM inbound WHERE user=? ORDER BY random() LIMIT 1`
		}
		err := in.store.r.QueryRow(q, u).Scan(&r.id, &r.uid, &r.kind, &r.event, &r.token, &r.run, &r.data, &r.t)
		if err == sql.ErrNoRows {
			s.mu.Lock()
			if s.stepKick {
				s.stepKick = false
				s.mu.Unlock()
				continue
			}
			s.stepActive = false
			s.mu.Unlock()
			return
		}
		if err != nil {
			if in.dead() {
				return
			}
			panic(err)
		}
		if err := in.processOne(u, r); err != nil {
			if err == errCrashed || in.dead() {
				return
			}
			if strings.Contains(err.Error(), "UNIQUE constraint failed: processed") {
				// only without the per-user lock: another worker processed this update meanwhile
				in.stats.dups.Add(1)
				in.store.tx(in, func(t txLike) error { _, err := t.Exec(`DELETE FROM inbound WHERE id=?`, r.id); return err })
				continue
			}
			panic(fmt.Sprintf("process %s %v: %v", u, r, err))
		}
	}
}

type userRow struct {
	bot    string
	snap   sql.NullString
	ver    int64
	hwm    int64
	exists bool
}

func (in *Instance) readUser(u string) userRow {
	var ur userRow
	err := in.store.r.QueryRow(`SELECT bot, snap, ver, hwm FROM users WHERE user=?`, u).Scan(&ur.bot, &ur.snap, &ur.ver, &ur.hwm)
	if err == nil {
		ur.exists = true
	} else if err != sql.ErrNoRows {
		panic(err)
	}
	return ur
}

func (in *Instance) readKV(u string) map[string]int64 {
	kv := map[string]int64{}
	rows, err := in.store.r.Query(`SELECT k, v FROM kv WHERE user=?`, u)
	if err != nil {
		panic(err)
	}
	defer rows.Close()
	for rows.Next() {
		var k string
		var v int64
		rows.Scan(&k, &v)
		kv[k] = v
	}
	return kv
}

func (in *Instance) drop(u string, r inRow, reason string) error {
	in.stats.drops.Add(1)
	return in.store.tx(in, func(t txLike) error {
		if _, err := t.Exec(`INSERT OR IGNORE INTO processed(user, uid) VALUES(?, ?)`, u, r.uid); err != nil {
			return err
		}
		if _, err := t.Exec(`INSERT INTO drops(user, uid, reason) VALUES(?, ?, ?)`, u, r.uid, reason); err != nil {
			return err
		}
		_, err := t.Exec(`DELETE FROM inbound WHERE id=?`, r.id)
		return err
	})
}

func (in *Instance) processOne(u string, r inRow) error {
	// 1. duplicate filtering
	if !in.cfg.noDedup {
		var one int
		err := in.store.r.QueryRow(`SELECT 1 FROM processed WHERE user=? AND uid=?`, u, r.uid).Scan(&one)
		if err == nil {
			in.stats.dups.Add(1)
			return in.store.tx(in, func(t txLike) error {
				_, err := t.Exec(`DELETE FROM inbound WHERE id=?`, r.id)
				return err
			})
		}
	}
	// 2. the barrier: sequential actions of earlier steps executed
	waitStart := time.Now()
	if !in.cfg.noBarrier {
		s := in.us(u)
		for {
			s.mu.Lock()
			n, ch := s.seqPending, s.barrier
			s.mu.Unlock()
			if n == 0 {
				break
			}
			select {
			case <-ch:
			case <-in.ctx.Done():
				return errCrashed
			}
		}
	}
	waitUs := time.Since(waitStart).Microseconds()
	for {
		m := in.stats.barrierWaitMaxUs.Load()
		if waitUs <= m || in.stats.barrierWaitMaxUs.CompareAndSwap(m, waitUs) {
			break
		}
	}
	in.stats.barrierWaitTotalUs.Add(waitUs)
	ur := in.readUser(u)
	bot := botOf(u)
	evData := map[string]any{}
	if r.data != "" && r.data != "null" {
		evData = decodeJSON(r.data).(map[string]any)
	}
	var pre any
	if ur.snap.Valid {
		pre = json.RawMessage(ur.snap.String)
	}
	req := map[string]any{}
	op := ""
	switch r.kind {
	case "tg":
		switch evData["support"] {
		case "lose": // "delete my data": the snapshot goes, the high-water mark stays
			return in.commitLose(u, r, ur)
		case "reset":
			if ur.snap.Valid {
				op = "restart"
				req = map[string]any{"op": "restart", "snapshot": pre}
			} else {
				op = "start"
				req = map[string]any{"op": "start", "seq": ur.hwm}
			}
		default:
			if ur.snap.Valid {
				op = "step"
				req = map[string]any{"op": "step", "snapshot": pre, "event": r.event}
			} else {
				op = "start"
				req = map[string]any{"op": "start", "event": r.event}
				if ur.hwm > 0 {
					req["seq"] = ur.hwm
				}
			}
		}
	case "timer", "outcome":
		if !ur.snap.Valid {
			return in.drop(u, r, "no snapshot")
		}
		// run id: the event must belong to the current run of the timer / call with this token
		name := ""
		kind := "start_timer"
		if r.kind == "timer" {
			name = strings.TrimPrefix(r.event, "timer:")
		} else {
			kind = "call"
			rest := strings.TrimPrefix(r.event, "call:")
			name = rest[:strings.LastIndex(rest, ":")]
		}
		var run string
		err := in.store.r.QueryRow(`SELECT run FROM outbox WHERE user=? AND kind=? AND name=? AND token=? ORDER BY id DESC LIMIT 1`,
			u, kind, name, r.token.Int64).Scan(&run)
		if err != nil || run != r.run.String {
			return in.drop(u, r, "run id mismatch")
		}
		op = "step"
		req = map[string]any{"op": "step", "snapshot": pre, "event": r.event, "token": r.token.Int64}
	default:
		panic(r.kind)
	}
	var kv map[string]int64
	guards := func(names []string) map[string]bool {
		if kv == nil {
			kv = in.readKV(u)
		}
		g := map[string]bool{}
		for _, n := range names {
			g[n] = guardValue(n, kv, evData)
		}
		return g
	}
	rep, err := in.reg.call(bot, req, guards)
	if err != nil {
		if strings.HasPrefix(err.Error(), "protocol violation") {
			return in.drop(u, r, err.Error()) // the failure queue; I6 counts it
		}
		return err
	}
	if rep.ErrCode != "" {
		in.stats.engineErrors.Add(1)
		return in.drop(u, r, "engine error "+rep.ErrCode+": "+rep.ErrText)
	}
	if err := in.crashPoint("step_before_commit"); err != nil {
		return err
	}
	return in.commitStep(u, r, ur, op, rep, evData, waitUs)
}

func actionLabel(a map[string]any) (kind, name string) {
	for _, k := range []string{"send", "call", "start_timer", "cancel_timer"} {
		if v, ok := a[k]; ok {
			return k, v.(string)
		}
	}
	panic(fmt.Sprint(a))
}

func numOf(v any) int64 {
	switch x := v.(type) {
	case json.Number:
		n, err := x.Int64()
		if err != nil {
			panic(err)
		}
		return n
	case int64:
		return x
	case float64:
		return int64(x)
	}
	panic(fmt.Sprintf("not a number: %T %v", v, v))
}

func (in *Instance) commitStep(u string, r inRow, ur userRow, op string, rep Reply, evData map[string]any, waitUs int64) error {
	snapJSON := mustJSON(rep.Snapshot)
	newSeq := int64(0)
	if s, ok := rep.Snapshot["seq"]; ok {
		newSeq = numOf(s)
	}
	type obRow struct {
		seq, pos            int64
		key, kind, name     string
		token               sql.NullInt64
		detached            int
		run                 sql.NullString
		ms, due             sql.NullInt64
	}
	var rows []obRow
	occ := map[string]int{}
	tcommit := nowMs()
	kb := r.uid
	if r.kind == "timer" || r.kind == "outcome" {
		kb = fmt.Sprintf("%s:%d:%s", r.event, r.token.Int64, r.run.String)
	}
	nSeqRows := 0
	var bgRows []int
	for i, a0 := range rep.Actions {
		a := a0.(map[string]any)
		kind, name := actionLabel(a)
		label := kind + ":" + name
		o := obRow{seq: newSeq, pos: int64(i), kind: kind, name: name}
		o.key = fmt.Sprintf("%s:%s:%s:%d", u, kb, label, occ[label])
		occ[label]++
		if t, ok := a["token"]; ok {
			o.token = sql.NullInt64{Int64: numOf(t), Valid: true}
			o.run = sql.NullString{String: newID(), Valid: true}
		}
		if d, ok := a["detached"]; ok && d == true {
			o.detached = 1
		}
		if ms, ok := a["ms"]; ok {
			m := numOf(ms)
			o.ms = sql.NullInt64{Int64: m, Valid: true}
			o.due = sql.NullInt64{Int64: tcommit + int64(float64(m)*in.cfg.timeScale), Valid: true}
		}
		if o.detached == 1 && !in.cfg.inlineBG {
			bgRows = append(bgRows, i)
		} else {
			nSeqRows++
		}
		rows = append(rows, o)
	}
	ids := make([]int64, len(rows))
	// the barrier counter is raised before the commit: once committed, the user's executor may
	// pick the new rows up at once (and lower the counter) before this goroutine continues
	in.addPending(u, nSeqRows)
	err := in.store.tx(in, func(t txLike) error {
		if _, err := t.Exec(`INSERT INTO processed(user, uid) VALUES(?, ?)`, u, r.uid); err != nil {
			return err
		}
		hwm := ur.hwm
		if newSeq > hwm {
			hwm = newSeq
		}
		var res sql.Result
		var err error
		if in.cfg.noLock {
			res, err = t.Exec(`UPDATE users SET snap=?, ver=ver+1, hwm=max(hwm, ?) WHERE user=?`, snapJSON, hwm, u)
		} else {
			res, err = t.Exec(`UPDATE users SET snap=?, ver=ver+1, hwm=? WHERE user=? AND ver=?`, snapJSON, hwm, u, ur.ver)
		}
		if err != nil {
			return err
		}
		if n, _ := res.RowsAffected(); n != 1 {
			return fmt.Errorf("lost update on %s (version %d)", u, ur.ver)
		}
		for i, o := range rows {
			res, err := t.Exec(`INSERT INTO outbox(user, seq, pos, key, kind, name, token, detached, run, ms, due, data, state, docv, t_commit)
				VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)`, u, o.seq, o.pos, o.key, o.kind, o.name, o.token, o.detached, o.run,
				o.ms, o.due, mustJSON(evData), rep.DocV, tcommit)
			if err != nil {
				return err
			}
			ids[i], _ = res.LastInsertId()
		}
		if !in.cfg.noSteplog {
			var pre any
			if ur.snap.Valid {
				pre = ur.snap.String
			}
			var tok any
			if r.token.Valid {
				tok = r.token.Int64
			}
			if _, err := t.Exec(`INSERT INTO steplog(user, op, uid, event, token, data, guards, docv, hwm, pre, post, actions, recovered, t_in, t_commit, wait_us)
				VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`, u, op, r.uid, r.event, tok, mustJSON(evData), mustJSON(rep.Guards),
				rep.DocV, ur.hwm, pre, snapJSON, mustJSON(rep.Actions), rep.Recovered, r.t, time.Now().UnixMicro(), waitUs); err != nil {
				return err
			}
		}
		_, err = t.Exec(`DELETE FROM inbound WHERE id=?`, r.id)
		return err
	})
	if err != nil {
		in.addPending(u, -nSeqRows)
		if err == errCrashed || in.dead() {
			return err
		}
		if strings.Contains(err.Error(), "UNIQUE constraint failed: processed") {
			// another worker processed this update id meanwhile (only possible without the
			// per-user lock): the row is a duplicate
			in.stats.dups.Add(1)
			return in.store.tx(in, func(t txLike) error {
				_, err := t.Exec(`DELETE FROM inbound WHERE id=?`, r.id)
				return err
			})
		}
		if strings.Contains(err.Error(), "lost update") {
			in.stats.engineErrors.Add(1)
			return nil // the snapshot changed under us: read it again and repeat the step
		}
		return err
	}
	in.stats.steps.Add(1)
	lat := time.Now().UnixMicro() - r.t
	in.stats.stepLatMu.Lock()
	in.stats.stepLat = append(in.stats.stepLat, lat)
	in.stats.stepLatMu.Unlock()
	if err := in.crashPoint("step_after_commit"); err != nil {
		return err
	}
	if nSeqRows > 0 {
		in.kickExec(u)
	}
	for _, i := range bgRows {
		in.enqueueBG(ids[i])
	}
	return nil
}

func (in *Instance) commitLose(u string, r inRow, ur userRow) error {
	return in.store.tx(in, func(t txLike) error {
		if _, err := t.Exec(`INSERT INTO processed(user, uid) VALUES(?, ?)`, u, r.uid); err != nil {
			return err
		}
		if _, err := t.Exec(`UPDATE users SET snap=NULL, ver=ver+1 WHERE user=?`, u); err != nil {
			return err
		}
		// the scheduler forgets the user's timers (all tokens are ≤ the high-water mark)
		if _, err := t.Exec(`DELETE FROM timers WHERE user=?`, u); err != nil {
			return err
		}
		if !in.cfg.noSteplog {
			var pre any
			if ur.snap.Valid {
				pre = ur.snap.String
			}
			if _, err := t.Exec(`INSERT INTO steplog(user, op, uid, event, hwm, pre, post, actions, t_in, t_commit) VALUES(?, 'lose', ?, ?, ?, ?, NULL, '[]', ?, ?)`,
				u, r.uid, r.event, ur.hwm, pre, r.t, time.Now().UnixMicro()); err != nil {
				return err
			}
		}
		_, err := t.Exec(`DELETE FROM inbound WHERE id=?`, r.id)
		return err
	})
}

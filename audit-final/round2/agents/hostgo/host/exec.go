package main

// Executors and the scheduler (docs/host-contract.md, sections 2 and 3).

import (
	"database/sql"
	"encoding/json"
	"fmt"
	"time"
)

type obRec struct {
	id       int64
	user     string
	seq, pos int64
	key      string
	kind     string
	name     string
	token    sql.NullInt64
	detached int
	run      sql.NullString
	ms, due  sql.NullInt64
	data     string
	state    int
}

const obCols = `id, user, seq, pos, key, kind, name, token, detached, run, ms, due, data, state`

func scanOb(sc interface{ Scan(...any) error }) (obRec, error) {
	var o obRec
	err := sc.Scan(&o.id, &o.user, &o.seq, &o.pos, &o.key, &o.kind, &o.name, &o.token, &o.detached, &o.run, &o.ms, &o.due, &o.data, &o.state)
	return o, err
}

func (in *Instance) kickExec(u string) {
	if in.dead() {
		return
	}
	s := in.us(u)
	s.mu.Lock()
	if s.execActive {
		s.execKick = true
		s.mu.Unlock()
		return
	}
	s.execActive = true
	s.mu.Unlock()
	in.goRun(func() { in.execLoop(u) })
}

// execLoop: the sequential actions of one user in commit order (seq, position). A sequential
// action that failed for good (state 2) stops the user's queue: it waits for a decision.
func (in *Instance) execLoop(u string) {
	s := in.us(u)
	for {
		if in.dead() {
			return
		}
		q := `SELECT ` + obCols + ` FROM outbox WHERE user=? AND detached=0 AND state IN (0, 2) ORDER BY seq, pos, id LIMIT 1`
		if in.cfg.inlineBG {
			q = `SELECT ` + obCols + ` FROM outbox WHERE user=? AND detached IN (0, 1) AND state IN (0, 2) ORDER BY seq, pos, id LIMIT 1`
		}
		o, err := scanOb(in.store.r.QueryRow(q, u))
		if err == sql.ErrNoRows || (err == nil && o.state == 2) {
			s.mu.Lock()
			if s.execKick {
				s.execKick = false
				s.mu.Unlock()
				continue
			}
			s.execActive = false
			s.mu.Unlock()
			return
		}
		if err != nil {
			if in.dead() {
				return
			}
			panic(err)
		}
		if in.cfg.execDelay > 0 {
			time.Sleep(in.cfg.execDelay)
		}
		status, err := in.execute(o, false)
		if err != nil {
			if err == errCrashed || in.dead() {
				return
			}
			panic(fmt.Sprintf("execute %v: %v", o, err))
		}
		if status == 1 {
			in.addPending(u, -1)
			in.kickStep(u)
		}
		// status 2: failed for good, the user's queue waits for a decision (the barrier stays)
	}
}

func (in *Instance) enqueueBG(id int64) {
	if in.dead() {
		return
	}
	if _, loaded := in.bgSet.LoadOrStore(id, true); loaded {
		return
	}
	select {
	case in.bgq <- id:
	default:
		panic("bg queue full")
	}
}

func (in *Instance) bgWorker() {
	for {
		select {
		case <-in.ctx.Done():
			return
		case id := <-in.bgq:
			o, err := scanOb(in.store.r.QueryRow(`SELECT `+obCols+` FROM outbox WHERE id=?`, id))
			if err != nil {
				if in.dead() {
					return
				}
				panic(err)
			}
			if o.state != 0 {
				in.bgSet.Delete(id)
				continue
			}
			if _, err := in.execute(o, true); err != nil {
				if err == errCrashed || in.dead() {
					return
				}
				panic(err)
			}
			in.bgSet.Delete(id)
		}
	}
}

func (in *Instance) markDone(t txLike, o obRec) error {
	res, err := t.Exec(`UPDATE outbox SET state=1, t_done=? WHERE id=? AND state=0`, time.Now().UnixMicro(), o.id)
	if err != nil {
		return err
	}
	if n, _ := res.RowsAffected(); n != 1 {
		return fmt.Errorf("outbox row %d already done", o.id)
	}
	return nil
}

func (in *Instance) cp(bg bool, point string) error {
	if bg {
		return in.crashPoint("bg_" + point)
	}
	return in.crashPoint("exec_" + point)
}

// execute runs one outbox row: 1 — done, 2 — failed for good (failure queue).
func (in *Instance) execute(o obRec, bg bool) (int, error) {
	ok := func(err error) (int, error) { return 1, err }
	switch o.kind {
	case "send":
		for attempt := 0; ; attempt++ {
			if in.dead() {
				return 0, errCrashed
			}
			if err := in.sims.tg.Send(o.user, o.name, o.seq); err == nil {
				break
			}
			time.Sleep(time.Millisecond)
		}
		return ok(in.store.tx(in, func(t txLike) error { return in.markDone(t, o) }))
	case "start_timer":
		// a start replaces the stored event only if its token is greater
		return ok(in.store.tx(in, func(t txLike) error {
			if _, err := t.Exec(`INSERT INTO timers(user, name, token, run, due) VALUES(?, ?, ?, ?, ?)
				ON CONFLICT(user, name) DO UPDATE SET token=excluded.token, run=excluded.run, due=excluded.due WHERE excluded.token > timers.token`,
				o.user, o.name, o.token.Int64, o.run.String, o.due.Int64); err != nil {
				return err
			}
			return in.markDone(t, o)
		}))
	case "cancel_timer":
		// remove only a scheduled event with a token smaller than the seq of the reply
		return ok(in.store.tx(in, func(t txLike) error {
			if _, err := t.Exec(`DELETE FROM timers WHERE user=? AND name=? AND token<?`, o.user, o.name, o.seq); err != nil {
				return err
			}
			return in.markDone(t, o)
		}))
	case "call":
	default:
		panic(o.kind)
	}
	data := map[string]any{}
	if o.data != "" && o.data != "null" {
		data = decodeJSON(o.data).(map[string]any)
	}
	if ownDB[o.name] {
		return ok(in.store.tx(in, func(t txLike) error {
			res, err := t.Exec(`INSERT OR IGNORE INTO effects(key, user, action) VALUES(?, ?, ?)`, o.key, o.user, o.name)
			if err != nil {
				return err
			}
			if n, _ := res.RowsAffected(); n == 1 {
				kv := map[string]int64{}
				rows, err := t.Query(`SELECT k, v FROM kv WHERE user=?`, o.user)
				if err != nil {
					return err
				}
				for rows.Next() {
					var k string
					var v int64
					rows.Scan(&k, &v)
					kv[k] = v
				}
				rows.Close()
				before := map[string]int64{}
				for k, v := range kv {
					before[k] = v
				}
				applyEffect(kv, o.name, data)
				for k, v := range kv {
					if bv, ok := before[k]; !ok || bv != v {
						if _, err := t.Exec(`INSERT INTO kv(user, k, v) VALUES(?, ?, ?) ON CONFLICT(user, k) DO UPDATE SET v=excluded.v`, o.user, k, v); err != nil {
							return err
						}
					}
				}
			}
			return in.markDone(t, o)
		}))
	}
	sys := extSystem[o.name]
	if sys == "" {
		panic("unknown action " + o.name)
	}
	sim := in.sims.of(sys)
	payload := map[string]any{}
	if sys == "crm" {
		kv := in.readKV(o.user)
		payload["cart"] = fmt.Sprintf("%s#%d", o.user, kv["cartgen"])
		payload["addr"] = data["text"]
		// the address of the order is the one saved by save_address (data of an earlier event);
		// the CRM rejects "zone-bad" addresses
		var addr string
		in.store.r.QueryRow(`SELECT data FROM outbox WHERE user=? AND kind='call' AND name='save_address' ORDER BY id DESC LIMIT 1`, o.user).Scan(&addr)
		if addr != "" {
			payload["addr"] = decodeJSON(addr).(map[string]any)["text"]
		}
	}
	if err := in.cp(bg, "before_external"); err != nil {
		return 0, err
	}
	var res ExtResult
	var callErr error
	for attempt := 0; attempt < 3; attempt++ {
		if in.dead() {
			return 0, errCrashed
		}
		key := o.key
		if in.cfg.noIdem {
			key = newID() // control: no idempotency key, every attempt is a new request
		}
		res, callErr = sim.Call(key, o.id, o.user, o.name, payload)
		if callErr == nil {
			break
		}
		time.Sleep(time.Duration(attempt+1) * 200 * time.Microsecond)
	}
	if err := in.cp(bg, "after_external"); err != nil {
		return 0, err
	}
	if !o.token.Valid {
		if callErr != nil { // failed for good: failure queue with an alert
			if bg {
				in.stats.bgFail.Add(1)
			} else {
				in.stats.seqFail.Add(1)
			}
			return 2, in.store.tx(in, func(t txLike) error {
				if _, err := t.Exec(`UPDATE outbox SET state=2 WHERE id=? AND state=0`, o.id); err != nil {
					return err
				}
				_, err := t.Exec(`INSERT INTO failq(outbox_id, user, name, detached, reason) VALUES(?, ?, ?, ?, ?)`, o.id, o.user, o.name, o.detached, callErr.Error())
				return err
			})
		}
		return ok(in.store.tx(in, func(t txLike) error { return in.markDone(t, o) }))
	}
	// an action with outcomes: exactly one outcome event with its token
	outcome, details := res.Outcome, res.Details
	via := "answer"
	if callErr != nil {
		outcome, via = "failed", "gave-up"
		details = map[string]any{"error": callErr.Error()}
		if !in.cfg.noRecon {
			in.stats.recons.Add(1)
			if r, ok := sim.Lookup(o.key); ok {
				in.stats.reconHits.Add(1)
				outcome, details, via = r.Outcome, r.Details, "reconciled"
			}
		}
		if outcome == "failed" {
			in.stats.failed.Add(1)
		}
	}
	if details == nil {
		details = map[string]any{}
	}
	ev := fmt.Sprintf("call:%s:%s", o.name, outcome)
	uid := fmt.Sprintf("call:%s:%d", o.name, o.token.Int64)
	dj, _ := json.Marshal(details)
	insertOutcome := func(t txLike) error {
		n := 1
		if in.cfg.dupProb > 0 && in.rand() < in.cfg.dupProb {
			n = 2 // a duplicated delivery of the outcome
		}
		for i := 0; i < n; i++ {
			if _, err := t.Exec(`INSERT INTO inbound(user, uid, kind, event, token, run, data, t) VALUES(?, ?, 'outcome', ?, ?, ?, ?, ?)`,
				o.user, uid, ev, o.token.Int64, o.run.String, string(dj), time.Now().UnixMicro()); err != nil {
				return err
			}
		}
		_, err := t.Exec(`INSERT INTO outcome_log(user, action, token, run, outcome, via) VALUES(?, ?, ?, ?, ?, ?)`,
			o.user, o.name, o.token.Int64, o.run.String, outcome, via)
		return err
	}
	if in.cfg.outcomeSeparate { // control: mark executed, then record the outcome in another step
		if err := in.store.tx(in, func(t txLike) error { return in.markDone(t, o) }); err != nil {
			return 0, err
		}
		if err := in.cp(bg, "between_mark_and_outcome"); err != nil {
			return 1, nil // the call is marked executed; its outcome is lost (the defect of this control)
		}
		if err := in.store.tx(in, insertOutcome); err != nil {
			return 1, nil
		}
	} else if err := in.store.tx(in, func(t txLike) error {
		if err := in.markDone(t, o); err != nil {
			return err
		}
		return insertOutcome(t)
	}); err != nil {
		return 0, err
	}
	if err := in.cp(bg, "after_record"); err != nil {
		return 1, nil // committed: done; the in-memory bookkeeping is rebuilt by recovery
	}
	in.kickStep(o.user)
	return 1, nil
}

// ---------------- scheduler ----------------

func (in *Instance) scheduler() {
	tick := time.NewTicker(2 * time.Millisecond)
	defer tick.Stop()
	for {
		select {
		case <-in.ctx.Done():
			return
		case <-tick.C:
		}
		rows, err := in.store.r.Query(`SELECT user, name, token, run FROM timers WHERE due<=? ORDER BY due LIMIT 500`, nowMs())
		if err != nil {
			if in.dead() {
				return
			}
			panic(err)
		}
		type tr struct {
			user, name, run string
			token           int64
		}
		var due []tr
		for rows.Next() {
			var t tr
			rows.Scan(&t.user, &t.name, &t.token, &t.run)
			due = append(due, t)
		}
		rows.Close()
		for _, t := range due {
			if err := in.crashPoint("sched_before_enqueue"); err != nil {
				return
			}
			err := in.store.tx(in, func(x txLike) error {
				res, err := x.Exec(`DELETE FROM timers WHERE user=? AND name=? AND token=? AND run=?`, t.user, t.name, t.token, t.run)
				if err != nil {
					return err
				}
				if n, _ := res.RowsAffected(); n != 1 {
					return nil // replaced or cancelled meanwhile
				}
				n := 1
				if in.cfg.dupProb > 0 && in.rand() < in.cfg.dupProb {
					n = 2
				}
				for i := 0; i < n; i++ {
					if _, err := x.Exec(`INSERT INTO inbound(user, uid, kind, event, token, run, data, t) VALUES(?, ?, 'timer', ?, ?, ?, '{}', ?)`,
						t.user, fmt.Sprintf("timer:%s:%d", t.name, t.token), "timer:"+t.name, t.token, t.run, time.Now().UnixMicro()); err != nil {
						return err
					}
				}
				return nil
			})
			if err != nil {
				if err == errCrashed || in.dead() {
					return
				}
				panic(err)
			}
			if err := in.crashPoint("sched_after_enqueue"); err != nil {
				return
			}
			in.kickStep(t.user)
		}
	}
}

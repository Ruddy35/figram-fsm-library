package host

import (
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"reflect"
	"sort"
	"sync"
	"time"
)

// Update is a validated incoming event (Telegram update or timer fire).
type Update struct {
	User     string
	UID      string // "tg:<update_id>" | "timer:<name>:<token>"
	Event    string
	Arg      *string
	Token    *int64
	TimerRow int64 // timers.id of a timer fire
}

type versionConflict struct{ msg string }

func (v *versionConflict) Error() string { return "version conflict: " + v.msg }

// ErrNack: the update is not acknowledged and will be redelivered.
type ErrNack struct{ Why string }

func (e *ErrNack) Error() string { return "nack: " + e.Why }

var errDrainFailed = errors.New("pending calls of the user could not be executed (rule G1)")

// Step processes one update under the user's lock. early (may be nil) lets a broken
// variant acknowledge before the commit.
func (h *Host) Step(u Update, early func(string)) (string, error) {
	if !h.broken["no-user-lock"] {
		l := h.lockOf(h.ulocks, u.User)
		l.Lock()
		defer l.Unlock()
	}
	var last error
	for attempt := 0; attempt < 8; attempt++ {
		st, err := h.stepOnce(u, early)
		if err == nil {
			return st, nil
		}
		var ef *EngineFailure
		var vc *versionConflict
		switch {
		case errors.As(err, &ef):
			h.stat("engine_retry", 1)
			h.log("engine_retry", u.User, map[string]any{"uid": u.UID, "error": trunc(err.Error(), 300)})
			last = err
		case errors.As(err, &vc):
			h.stat("version_conflict", 1)
			last = err
		default:
			return "", err
		}
	}
	return "", &ErrNack{fmt.Sprintf("retries exhausted: %v", last)}
}

func trunc(s string, n int) string {
	if len(s) > n {
		return s[:n]
	}
	return s
}

type stepCtx struct {
	u         Update
	exists    bool
	family    string
	doc       string
	snapTxt   []byte
	snap      *Snapshot
	version   int64
	hw        int64
	kind      string
	note      string
	guards    map[string]bool
	reply     *Reply
	seqBefore int64
}

func (h *Host) stepOnce(u Update, early func(string)) (string, error) {
	db := h.db
	nodedup := h.broken["no-dedup"]
	if !nodedup {
		var one int
		err := db.QueryRow(`SELECT 1 FROM processed WHERE user=? AND update_id=?`, u.User, u.UID).Scan(&one)
		if err == nil {
			h.stat("dup", 1)
			return "dup", nil
		}
		if !errors.Is(err, sql.ErrNoRows) {
			return "", err
		}
	}
	c := &stepCtx{u: u}
	var snapNull sql.NullString
	err := db.QueryRow(`SELECT family, doc, snapshot, version, hw_seq FROM users WHERE user=?`, u.User).
		Scan(&c.family, &c.doc, &snapNull, &c.version, &c.hw)
	switch {
	case errors.Is(err, sql.ErrNoRows):
		c.family = h.cfg.Family
		c.doc = h.FamilyDoc(c.family)
	case err != nil:
		return "", err
	default:
		c.exists = true
		if snapNull.Valid {
			c.snapTxt = []byte(snapNull.String)
		}
	}
	var held []*sync.Mutex
	defer func() {
		for i := len(held) - 1; i >= 0; i-- {
			held[i].Unlock()
		}
	}()

	if !c.exists {
		r, err := h.eng.Call(c.doc, map[string]any{"op": "start"}, -1)
		if err != nil {
			return "", err
		}
		if r.Kind != "result" {
			return "", &EngineFailure{"start did not return a snapshot: " + r.Err}
		}
		c.kind, c.reply = "start", r
	} else {
		c.seqBefore = c.hw
		bad := ""
		if h.broken["trust-snapshot"] {
			if c.snapTxt == nil {
				return h.commitError(c, "snapshot is NULL")
			}
			if _, perr := ParseStrict(c.snapTxt); perr != nil {
				return h.commitError(c, "snapshot is not JSON: "+perr.Error())
			}
			if s, e := ParseSnapshotText(c.snapTxt); e == nil {
				c.snap = s
			}
		} else {
			bad = h.checkStored(c)
		}
		if bad != "" {
			r, err := h.eng.Call(c.doc, map[string]any{"op": "restart",
				"snapshot": map[string]any{"at": "-", "seq": c.hw}}, c.hw)
			if err != nil {
				return "", err
			}
			if r.Kind != "result" {
				return h.commitError(c, "repair failed: "+r.Err)
			}
			c.kind, c.note, c.reply = "repair", bad, r
			h.stat("repair", 1)
			h.log("repair", u.User, map[string]any{"why": bad, "hw": c.hw})
		} else {
			req := map[string]any{"op": "step", "snapshot": json.RawMessage(c.snapTxt), "event": u.Event}
			if u.Token != nil {
				tok := *u.Token
				if h.broken["stale-token-lookup"] && c.snap != nil {
					if cur, ok := c.snap.Timers[u.Event[len("timer:"):]]; ok {
						tok = cur // BROKEN: whatever token is current, not the fired one
					}
				}
				req["token"] = tok
			}
			c.kind = "step"
			r, err := h.eng.Call(c.doc, req, c.hw)
			if err != nil {
				return "", err
			}
			if r.Kind == "need" {
				if !h.broken["no-drain"] {
					if err := h.drainUser(u.User); err != nil { // rule G1
						return "", err
					}
				}
				if !h.broken["no-resource-lock"] { // rule G2
					var res []string
					for _, n := range r.Need {
						name, err := GuardResource(db, n, u.User)
						if err != nil {
							return "", err
						}
						if name != "" {
							res = append(res, name)
						}
					}
					sort.Strings(res)
					for i, name := range res {
						if i > 0 && res[i-1] == name {
							continue
						}
						l := h.lockOf(h.rlocks, name)
						l.Lock()
						held = append(held, l)
					}
				}
				g, err := h.computeGuards(r.Need, u)
				if err != nil {
					return "", err
				}
				c.guards = g
				req["guards"] = g
				r, err = h.eng.Call(c.doc, req, c.hw)
				if err != nil {
					return "", err
				}
				if r.Kind == "need" {
					return "", &EngineFailure{fmt.Sprintf("engine asked for guards twice: %v", r.Need)}
				}
			}
			if r.Kind == "error" {
				return h.commitError(c, r.Err) // deterministic: consume, never a poison loop
			}
			c.reply = r
		}
	}
	// commit-time parameters read the domain: same rule as guards (G1)
	if c.guards == nil && !h.broken["no-drain"] {
		for _, a := range c.reply.Actions {
			if a.Kind == "call" && Calls[a.Name].Param == "selection" {
				if err := h.drainUser(u.User); err != nil {
					return "", err
				}
				break
			}
		}
	}
	if err := h.checkSeq(c); err != nil {
		return "", err
	}
	return h.commit(c, early)
}

// checkStored validates the stored snapshot: schema + persistent high-water seq.
func (h *Host) checkStored(c *stepCtx) string {
	if c.snapTxt == nil {
		return "snapshot is NULL"
	}
	s, err := ParseSnapshotText(c.snapTxt)
	if err != nil {
		return err.Error()
	}
	if s.Seq != c.hw {
		return fmt.Sprintf("snapshot seq %d != committed high-water seq %d", s.Seq, c.hw)
	}
	c.snap = s
	return ""
}

// checkSeq: FORMAT §7 seq rules, checked on every reply (a corrupted reply that is
// schema-valid would otherwise be committed).
func (h *Host) checkSeq(c *stepCtx) error {
	if !h.eng.Validate {
		return nil
	}
	r := c.reply
	switch c.kind {
	case "start":
		if r.Snap.Seq != 1 {
			return &EngineFailure{fmt.Sprintf("start returned seq %d", r.Snap.Seq)}
		}
	case "repair":
		if r.Snap.Seq != c.hw+1 {
			return &EngineFailure{fmt.Sprintf("restart returned seq %d, want %d", r.Snap.Seq, c.hw+1)}
		}
	case "step":
		if c.snap == nil {
			return nil
		}
		if h.changed(c) {
			if r.Snap.Seq != c.snap.Seq+1 {
				return &EngineFailure{fmt.Sprintf("transition seq %d after %d", r.Snap.Seq, c.snap.Seq)}
			}
		} else if r.Snap.Seq != c.snap.Seq {
			return &EngineFailure{"ignored step changed seq"}
		}
	}
	for _, a := range r.Actions {
		if a.Kind == "start_timer" && a.Token != r.Snap.Seq {
			return &EngineFailure{fmt.Sprintf("timer token %d != seq %d", a.Token, r.Snap.Seq)}
		}
	}
	return nil
}

func (h *Host) changed(c *stepCtx) bool {
	if c.kind != "step" {
		return true
	}
	if len(c.reply.Actions) > 0 || c.snap == nil {
		return true
	}
	return !reflect.DeepEqual(c.snap, c.reply.Snap)
}

func (h *Host) computeGuards(need []string, u Update) (map[string]bool, error) {
	out := map[string]bool{}
	arg := ""
	if u.Arg != nil {
		arg = *u.Arg
	}
	for _, n := range need {
		if h.broken["const-guards"] {
			out[n] = true
			continue
		}
		v, err := EvalGuard(h.db, n, GuardCtx{User: u.User, Arg: arg, CountIntents: !h.broken["no-resource-lock"]})
		if err != nil {
			return nil, err
		}
		out[n] = v
	}
	return out, nil
}

func (h *Host) commitError(c *stepCtx, msg string) (string, error) {
	tx, err := h.db.Begin()
	if err != nil {
		return "", err
	}
	defer tx.Rollback()
	if _, err := tx.Exec(`INSERT OR REPLACE INTO processed(user,update_id,status,t_ms) VALUES(?,?,?,?)`,
		c.u.User, c.u.UID, "error", h.now()); err != nil {
		return "", err
	}
	if c.u.TimerRow != 0 {
		tx.Exec(`UPDATE timers SET delivered=1 WHERE id=?`, c.u.TimerRow)
	}
	if err := tx.Commit(); err != nil {
		return "", err
	}
	h.stat("engine_error", 1)
	h.log("engine_error", c.u.User, map[string]any{"uid": c.u.UID, "error": trunc(msg, 300)})
	return "error", nil
}

func nullStr(p *string) any {
	if p == nil {
		return nil
	}
	return *p
}

func nullInt(p *int64) any {
	if p == nil {
		return nil
	}
	return *p
}

func (h *Host) commit(c *stepCtx, early func(string)) (string, error) {
	r := c.reply
	u := c.u
	newSeq := r.Snap.Seq
	changed := h.changed(c)
	status := "ok"
	switch {
	case r.Recovered:
		status = "recovered"
	case c.kind == "repair":
		status = "repair"
	case !changed:
		status = "ignored"
	}
	if h.broken["ack-before-commit"] && early != nil {
		early(status) // BROKEN: acknowledged before the transaction is durable
	}
	now := h.now()
	tx, err := h.db.Begin()
	if err != nil {
		return "", err
	}
	defer tx.Rollback()
	if !h.broken["no-dedup"] {
		var one int
		if err := tx.QueryRow(`SELECT 1 FROM processed WHERE user=? AND update_id=?`, u.User, u.UID).Scan(&one); err == nil {
			return "dup", nil
		} else if !errors.Is(err, sql.ErrNoRows) {
			return "", err
		}
	}
	snapText := string(r.SnapText)
	switch {
	case !c.exists:
		if _, err := tx.Exec(`INSERT INTO users(user,family,doc,snapshot,version,hw_seq,created_ms,updated_ms)
			VALUES(?,?,?,?,1,?,?,?)`, u.User, c.family, c.doc, snapText, newSeq, now, now); err != nil {
			if IsConstraint(err) {
				return "", &versionConflict{"user created concurrently"}
			}
			return "", err
		}
	case h.broken["no-user-lock"]:
		if _, err := tx.Exec(`UPDATE users SET snapshot=?, version=version+1, hw_seq=?, updated_ms=? WHERE user=?`,
			snapText, newSeq, now, u.User); err != nil {
			return "", err
		}
	default:
		res, err := tx.Exec(`UPDATE users SET snapshot=?, version=version+1, hw_seq=MAX(hw_seq,?), updated_ms=?
			WHERE user=? AND version=?`, snapText, newSeq, now, u.User, c.version)
		if err != nil {
			return "", err
		}
		if n, _ := res.RowsAffected(); n != 1 {
			return "", &versionConflict{"users.version changed"}
		}
	}
	if _, err := tx.Exec(`INSERT OR REPLACE INTO processed(user,update_id,status,t_ms) VALUES(?,?,?,?)`,
		u.User, u.UID, status, now); err != nil {
		return "", err
	}
	if changed || c.guards != nil {
		var gtxt any
		if c.guards != nil {
			b, _ := json.Marshal(c.guards)
			gtxt = string(b)
		}
		var note any
		if c.note != "" {
			note = c.note
		}
		acts := string(r.ActsText)
		if acts == "" {
			acts = "[]"
		}
		if _, err := tx.Exec(`INSERT INTO transitions(user,update_id,event,arg,token,kind,seq_before,seq,at,doc,snapshot,
			actions,recovered,t_ms,guards,changed,note) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`,
			u.User, u.UID, u.Event, nullStr(u.Arg), nullInt(u.Token), c.kind, c.seqBefore, newSeq, r.Snap.At, c.doc,
			snapText, acts, boolInt(r.Recovered), now, gtxt, boolInt(changed), note); err != nil {
			return "", err
		}
	}
	if c.kind == "repair" {
		if _, err := tx.Exec(`UPDATE timers SET state='canceled' WHERE user=? AND state='scheduled'`, u.User); err != nil {
			return "", err
		}
	}
	if u.TimerRow != 0 {
		if _, err := tx.Exec(`UPDATE timers SET delivered=1, fired_ms=?, state=CASE WHEN state='scheduled' THEN 'fired' ELSE state END
			WHERE id=?`, now, u.TimerRow); err != nil {
			return "", err
		}
	}
	split := h.broken["outbox-after-ack"]
	if !split {
		if err := h.insertOutbox(tx, u.User, newSeq, r.Actions, u.Arg, now); err != nil {
			return "", err
		}
	}
	h.crashPoint("before_commit", u.Event)
	if err := tx.Commit(); err != nil {
		return "", err
	}
	if r.Recovered {
		h.stat("recovered", 1)
		h.log("recovered", u.User, map[string]any{"uid": u.UID, "doc": c.doc})
	}
	if split {
		if early != nil {
			early(status) // BROKEN: ack before the outbox rows are durable
		}
		h.crashPoint("after_commit", u.Event)
		tx2, err := h.db.Begin()
		if err != nil {
			return status, nil
		}
		if err := h.insertOutbox(tx2, u.User, newSeq, r.Actions, u.Arg, now); err != nil {
			tx2.Rollback()
			return status, nil
		}
		tx2.Commit()
	} else {
		h.crashPoint("after_commit", u.Event)
	}
	if len(r.Actions) > 0 {
		h.exec.notify(u.User)
	}
	return status, nil
}

func boolInt(b bool) int {
	if b {
		return 1
	}
	return 0
}

// Key scheme: "<user>:<seq>:<index>". seq is the snapshot seq after the transition
// and is monotone over the whole lifetime of the user: `start` only for a user
// without a row, every reset/repair is `restart` from the persistent high-water seq
// (users.hw_seq, only ever raised with MAX), user rows are never deleted. The UNIQUE
// constraint on outbox.key is a tripwire: a collision aborts the transaction.
func outboxKey(user string, seq int64, i int) string { return fmt.Sprintf("%s:%d:%d", user, seq, i) }

func (h *Host) insertOutbox(tx *sql.Tx, user string, seq int64, acts []Action, arg *string, now int64) error {
	for i, a := range acts {
		key := outboxKey(user, seq, i)
		switch a.Kind {
		case "call":
			spec, ok := Calls[a.Name]
			var param sql.NullString
			if ok {
				p, err := ResolveParam(tx, spec, user, arg)
				if err != nil {
					return err
				}
				param = p
			}
			if _, err := tx.Exec(`INSERT INTO outbox(key,user,seq,idx,kind,name,param,state,created_ms)
				VALUES(?,?,?,?,'call',?,?,'pending',?)`, key, user, seq, i, a.Name, param, now); err != nil {
				return err
			}
		case "send":
			if _, err := tx.Exec(`INSERT INTO outbox(key,user,seq,idx,kind,name,state,created_ms)
				VALUES(?,?,?,?,'send',?,'pending',?)`, key, user, seq, i, a.Name, now); err != nil {
				return err
			}
		case "cancel_timer":
			if _, err := tx.Exec(`UPDATE timers SET state='canceled' WHERE user=? AND name=? AND state='scheduled'`, user, a.Name); err != nil {
				return err
			}
			if _, err := tx.Exec(`INSERT INTO outbox(key,user,seq,idx,kind,name,state,created_ms,done_ms)
				VALUES(?,?,?,?,'cancel_timer',?,'done',?,?)`, key, user, seq, i, a.Name, now, now); err != nil {
				return err
			}
		case "start_timer":
			if _, err := tx.Exec(`UPDATE timers SET state='canceled' WHERE user=? AND name=? AND state='scheduled'`, user, a.Name); err != nil {
				return err
			}
			if _, err := tx.Exec(`INSERT INTO timers(user,name,token,ms,due_ms,state,created_ms) VALUES(?,?,?,?,?,'scheduled',?)`,
				user, a.Name, a.Token, a.Ms, now+a.Ms, now); err != nil {
				return err
			}
			if _, err := tx.Exec(`INSERT INTO outbox(key,user,seq,idx,kind,name,state,created_ms,done_ms)
				VALUES(?,?,?,?,'start_timer',?,'done',?,?)`, key, user, seq, i, a.Name, now, now); err != nil {
				return err
			}
		}
	}
	return nil
}

// sleepBackoff caps a retry delay.
func sleepBackoff(ms int64) {
	if ms <= 0 {
		ms = 10
	}
	if ms > 200 {
		ms = 200
	}
	time.Sleep(time.Duration(ms) * time.Millisecond)
}

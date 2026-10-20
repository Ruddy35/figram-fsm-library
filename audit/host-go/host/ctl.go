package host

import (
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"runtime"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"

	"figramhost/figram"
)

// ---------------------------------------------------------------- timers

type timerRow struct {
	id    int64
	user  string
	name  string
	token int64
	due   int64
}

func (h *Host) setClock(t int64) error {
	h.clockMu.Lock()
	if t <= h.clock {
		h.clockMu.Unlock()
		return nil
	}
	h.clock = t
	h.clockMu.Unlock()
	for i := 0; ; i++ {
		tx, err := h.beginW()
		if err == nil {
			if err = setMeta(tx.Tx, "clock_ms", strconv.FormatInt(t, 10)); err == nil {
				err = tx.Commit()
			} else {
				tx.Rollback()
			}
		}
		if err == nil || i > 100 {
			return err
		}
		time.Sleep(50 * time.Millisecond)
	}
}

// AdvanceTo fires every due timer in (due, user, name, token) order on the simulated
// clock through the normal step path; update id timer:<name>:<token>.
func (h *Host) AdvanceTo(target int64) error {
	stuck := 0
	for {
		states := "('scheduled')"
		if h.ignoreCanc.Load() {
			states = "('scheduled','canceled')"
		}
		rows, err := h.db.Query(`SELECT id,user,name,token,due_ms FROM timers WHERE delivered=0 AND due_ms<=? AND state IN `+
			states+` ORDER BY due_ms,user,name,token,id`, target)
		if err != nil {
			if IsBusy(err) && stuck < 200 {
				stuck++
				time.Sleep(50 * time.Millisecond)
				continue
			}
			return err
		}
		var batch []timerRow
		for rows.Next() {
			var t timerRow
			if err := rows.Scan(&t.id, &t.user, &t.name, &t.token, &t.due); err != nil {
				rows.Close()
				return err
			}
			if len(batch) > 0 && t.due != batch[0].due {
				break
			}
			batch = append(batch, t)
		}
		rows.Close()
		if len(batch) == 0 {
			break
		}
		if err := h.setClock(batch[0].due); err != nil {
			return err
		}
		if !h.deliverTimers(batch, int(h.timerDup.Load()), "") {
			stuck++
			if stuck > 200 {
				return errors.New("timer delivery keeps failing")
			}
			time.Sleep(50 * time.Millisecond)
		}
	}
	return h.setClock(target)
}

func (h *Host) deliverTimers(rows []timerRow, dup int, suffix string) bool {
	if dup < 1 {
		dup = 1
	}
	per := map[string][]timerRow{}
	var users []string
	for _, r := range rows {
		if per[r.user] == nil {
			users = append(users, r.user)
		}
		per[r.user] = append(per[r.user], r)
	}
	var wg sync.WaitGroup
	var mu sync.Mutex
	ok := true
	for _, u := range users {
		for k := 0; k < dup; k++ {
			wg.Add(1)
			go func(list []timerRow) {
				defer wg.Done()
				for _, t := range list {
					tok := t.token
					up := Update{User: t.user, UID: fmt.Sprintf("timer:%s:%d%s", t.name, t.token, suffix),
						Event: "timer:" + t.name, Token: &tok, TimerRow: t.id}
					st, err := h.Step(up, nil)
					if err != nil {
						mu.Lock()
						ok = false
						mu.Unlock()
						continue
					}
					if st == "dup" {
						h.execW(`UPDATE timers SET delivered=1 WHERE id=?`, t.id)
					}
					h.stat("timer_"+st, 1)
				}
			}(per[u])
		}
	}
	wg.Wait()
	return ok
}

// ReplayStale re-delivers every canceled/orphaned/fired timer of a user with its
// original update id (stale callbacks that arrive anyway).
func (h *Host) ReplayStale(user string) (int, error) {
	rows, err := h.db.Query(`SELECT id,user,name,token,due_ms FROM timers WHERE user=? AND state IN ('canceled','orphaned','fired') ORDER BY id`, user)
	if err != nil {
		return 0, err
	}
	var list []timerRow
	for rows.Next() {
		var t timerRow
		rows.Scan(&t.id, &t.user, &t.name, &t.token, &t.due)
		list = append(list, t)
	}
	rows.Close()
	h.deliverTimers(list, 1, "")
	return len(list), nil
}

// ---------------------------------------------------------------- barrier

// Barrier waits until no update is in flight and the outbox is drained (unless the
// executor is paused).
func (h *Host) Barrier(timeout time.Duration) error {
	end := time.Now().Add(timeout)
	for h.inflightN.Load() > 0 {
		if time.Now().After(end) {
			return errors.New("barrier timeout (in-flight updates)")
		}
		time.Sleep(5 * time.Millisecond)
	}
	for !h.exec.paused.Load() {
		var n int
		if err := h.db.QueryRow(`SELECT COUNT(*) FROM outbox WHERE state='pending'`).Scan(&n); err != nil {
			n = -1
		}
		if n == 0 && h.exec.busy.Load() == 0 {
			return nil
		}
		if time.Now().After(end) {
			return fmt.Errorf("barrier timeout (%d pending outbox rows)", n)
		}
		h.exec.kickAll()
		time.Sleep(10 * time.Millisecond)
	}
	return nil
}

// ---------------------------------------------------------------- reset / upgrade

// ResetUser: "delete my data, keep the account" — restart from the stored snapshot
// (or {"at":"-","seq":hw}), never start (ET-01); cancel every scheduled timer.
func (h *Host) ResetUser(user, ctlID string) (string, error) {
	l := h.lockOf(h.ulocks, user)
	l.Lock()
	defer l.Unlock()
	var family, doc string
	var snapNull sql.NullString
	var version, hw int64
	err := h.db.QueryRow(`SELECT family, doc, snapshot, version, hw_seq FROM users WHERE user=?`, user).
		Scan(&family, &doc, &snapNull, &version, &hw)
	if errors.Is(err, sql.ErrNoRows) {
		return "no_user", nil
	}
	if err != nil {
		return "", err
	}
	uid := "reset:" + ctlID
	var one int
	if h.db.QueryRow(`SELECT 1 FROM processed WHERE user=? AND update_id=?`, user, uid).Scan(&one) == nil {
		return "dup", nil
	}
	var bad string
	var snapTxt []byte
	if snapNull.Valid {
		snapTxt = []byte(snapNull.String)
		if s, err := ParseSnapshotText(snapTxt); err != nil {
			bad = err.Error()
		} else if s.Seq != hw {
			bad = "seq != high-water"
		}
	} else {
		bad = "snapshot is NULL"
	}
	var r *Reply
	seqKeysStart := h.broken["seq-keys-start"]
	for attempt := 0; attempt < 8; attempt++ {
		if seqKeysStart {
			r, err = h.eng.Call(doc, map[string]any{"op": "start"}, -1)
		} else if bad == "" {
			r, err = h.eng.Call(doc, map[string]any{"op": "restart", "snapshot": json.RawMessage(snapTxt)}, hw)
		} else {
			r, err = h.eng.Call(doc, map[string]any{"op": "restart", "snapshot": map[string]any{"at": "-", "seq": hw}}, hw)
		}
		if err == nil {
			break
		}
	}
	if err != nil {
		return "", err
	}
	if r.Kind != "result" {
		return "", fmt.Errorf("reset: engine error %s", r.Err)
	}
	newSeq := r.Snap.Seq
	now := h.now()
	tx, err := h.beginW()
	if err != nil {
		return "", err
	}
	defer tx.Rollback()
	if err := ResetUserData(tx.Tx, family, user); err != nil {
		return "", err
	}
	if seqKeysStart {
		// BROKEN (ET-01 path): start + forget the outbox history so keys can be reused
		tx.Exec(`DELETE FROM outbox WHERE user=? AND state<>'pending'`, user)
		if _, err := tx.Exec(`UPDATE users SET snapshot=?, version=version+1, hw_seq=? WHERE user=?`, string(r.SnapText), newSeq, user); err != nil {
			return "", err
		}
	} else {
		if _, err := tx.Exec(`UPDATE timers SET state='canceled' WHERE user=? AND state='scheduled'`, user); err != nil {
			return "", err
		}
		if _, err := tx.Exec(`UPDATE users SET snapshot=?, version=version+1, hw_seq=MAX(hw_seq,?) WHERE user=?`, string(r.SnapText), newSeq, user); err != nil {
			return "", err
		}
	}
	if _, err := tx.Exec(`INSERT OR REPLACE INTO processed(user,update_id,status,t_ms) VALUES(?,?,?,?)`, user, uid, "reset", now); err != nil {
		return "", err
	}
	var note any
	if bad != "" {
		note = bad
	}
	if _, err := tx.Exec(`INSERT INTO transitions(user,update_id,event,kind,seq_before,seq,at,doc,snapshot,actions,recovered,t_ms,changed,note)
		VALUES(?,?,?,?,?,?,?,?,?,?,0,?,1,?)`, user, uid, "reset", "reset", hw, newSeq, r.Snap.At, doc, string(r.SnapText),
		string(r.ActsText), now, note); err != nil {
		return "", err
	}
	if err := h.insertOutbox(tx.Tx, user, newSeq, r.Actions, nil, now); err != nil {
		return "", err
	}
	if err := tx.Commit(); err != nil {
		return "", err
	}
	h.exec.notify(user)
	return "ok", nil
}

// Upgrade switches a bot family to a new document version: the new document is
// loaded (figram_load_doc) before anything changes; users are switched atomically
// (version bump: racing steps on the old document retry on the new one); scheduled
// timers whose name does not exist in the new document are orphaned (ET-02
// mitigation); the old automaton is unloaded after its in-flight calls finish.
func (h *Host) Upgrade(newDoc string) (map[string]any, error) {
	family := familyOf(newDoc)
	di, err := h.eng.Info(newDoc)
	if err != nil {
		return nil, fmt.Errorf("new document does not load, keeping the old one: %w", err)
	}
	oldDoc := h.FamilyDoc(family)
	names := di.TimerNames()
	tx, err := h.beginW()
	if err != nil {
		return nil, err
	}
	defer tx.Rollback()
	res, err := tx.Exec(`UPDATE users SET doc=?, version=version+1 WHERE family=?`, newDoc, family)
	if err != nil {
		return nil, err
	}
	nUsers, _ := res.RowsAffected()
	if err := setMeta(tx.Tx, "doc:"+family, newDoc); err != nil {
		return nil, err
	}
	ph := strings.TrimSuffix(strings.Repeat("?,", len(names)), ",")
	if ph == "" {
		ph = "''"
	}
	args := []any{}
	for _, n := range names {
		args = append(args, n)
	}
	args = append(args, family)
	res, err = tx.Exec(`UPDATE timers SET state='orphaned' WHERE state='scheduled' AND name NOT IN (`+ph+`)
		AND user IN (SELECT user FROM users WHERE family=?)`, args...)
	if err != nil {
		return nil, err
	}
	nOrphan, _ := res.RowsAffected()
	if err := tx.Commit(); err != nil {
		return nil, err
	}
	h.famDocMu.Lock()
	h.famDoc[family] = newDoc
	h.famDocMu.Unlock()
	if oldDoc != newDoc {
		h.eng.Drop(oldDoc)
	}
	h.log("upgrade", "", map[string]any{"doc": newDoc, "users": nUsers, "orphaned_timers": nOrphan})
	h.stat("upgrades", 1)
	h.stat("orphaned_timers", nOrphan)
	return map[string]any{"users": nUsers, "orphaned_timers": nOrphan, "old": oldDoc}, nil
}

// ---------------------------------------------------------------- ctl dispatcher

// Ctl executes one control command. Every command is safe to re-send after a
// crash (reset is deduplicated by its ctl id, advance_to is absolute).
func (h *Host) Ctl(cmd map[string]any) (map[string]any, error) {
	k, _ := cmd["ctl"].(string)
	str := func(key string) string { s, _ := cmd[key].(string); return s }
	num := func(key string) int64 {
		switch v := cmd[key].(type) {
		case float64:
			return int64(v)
		case json.Number:
			n, _ := v.Int64()
			return n
		}
		return 0
	}
	out := map[string]any{}
	switch k {
	case "barrier":
		return out, h.Barrier(120 * time.Second)
	case "crash":
		h.Arm(CrashPoint{At: str("at"), On: str("on")})
	case "advance_to":
		if err := h.Barrier(120 * time.Second); err != nil {
			return nil, err
		}
		if err := h.AdvanceTo(num("t")); err != nil {
			return nil, err
		}
		if err := h.Barrier(120 * time.Second); err != nil {
			return nil, err
		}
		out["clock"] = h.now()
	case "upgrade":
		if err := h.Barrier(120 * time.Second); err != nil {
			return nil, err
		}
		return h.Upgrade(str("bot"))
	case "reload_doc":
		d, err := h.eng.Reload(str("doc"))
		if err != nil {
			return nil, err
		}
		out["load_ms"] = float64(d.Microseconds()) / 1000
	case "executor":
		if str("mode") == "pause" {
			h.exec.paused.Store(true)
		} else {
			h.exec.paused.Store(false)
			h.exec.kickAll()
		}
	case "timer_mode":
		tx, err := h.beginW()
		if err != nil {
			return nil, err
		}
		if v, ok := cmd["dup"]; ok {
			_ = v
			n := num("dup")
			setMeta(tx.Tx, "timer_dup", strconv.FormatInt(n, 10))
			h.timerDup.Store(n)
		}
		if v, ok := cmd["ignore_cancel"].(bool); ok {
			setMeta(tx.Tx, "ignore_cancel", map[bool]string{true: "1", false: "0"}[v])
			h.ignoreCanc.Store(v)
		}
		if err := tx.Commit(); err != nil {
			return nil, err
		}
	case "replay_stale":
		if err := h.Barrier(120 * time.Second); err != nil {
			return nil, err
		}
		n, err := h.ReplayStale(str("user"))
		if err != nil {
			return nil, err
		}
		out["replayed"] = n
		if err := h.Barrier(120 * time.Second); err != nil {
			return nil, err
		}
	case "reset_user":
		if err := h.Barrier(120 * time.Second); err != nil {
			return nil, err
		}
		st, err := h.ResetUser(str("user"), str("id"))
		if err != nil {
			return nil, err
		}
		out["status"] = st
		if err := h.Barrier(120 * time.Second); err != nil {
			return nil, err
		}
	case "engine_fault":
		h.faults.Arm(str("mode"))
	case "stats":
		return h.StatsReport(), nil
	default:
		return nil, fmt.Errorf("unknown ctl %q", k)
	}
	return out, nil
}

// StatsReport: counters + runtime numbers of this process (embedded engine included).
func (h *Host) StatsReport() map[string]any {
	inj, det := h.faults.Snapshot()
	bs := figram.GetStats()
	var ms runtime.MemStats
	runtime.ReadMemStats(&ms)
	st := h.Stats()
	keys := make([]string, 0, len(st))
	for k := range st {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	return map[string]any{
		"stats":           st,
		"faults_injected": inj, "faults_detected": det,
		"goroutines": runtime.NumGoroutine(),
		"go_heap_alloc": ms.HeapAlloc, "go_sys": ms.Sys, "num_gc": ms.NumGC,
		"binding": map[string]any{"calls": bs.Calls, "c_nanos": bs.Nanos, "in_bytes": bs.InBytes,
			"out_bytes": bs.OutBytes, "loads": bs.Loads, "unloads": bs.Unloads, "live_machines": bs.Live},
		"engine_calls":   h.eng.Calls.Load(),
		"retired":        h.eng.Retired.Load(),
		"init":           figram.InitReport(),
		"clock":          h.now(),
		"exec_paused":    h.exec.paused.Load(),
	}
}

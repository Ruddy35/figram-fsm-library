package main

// End-of-run invariants of a production host, computed from the store, the step log and the
// ground truth of the simulated systems:
//   I1 at most one irreversible effect per logical action (and per cart for the CRM);
//   I2 exactly one outcome event per call with a token; exactly one accepted outcome per call
//      unless its wait was legitimately replaced (latest) or cleared (restart, lost snapshot);
//   I3 no stuck users: nothing left in the inbound queue or the outbox, no wait left in a
//      snapshot, no sequential action in the failure queue, nobody on "please wait" without a wait;
//   I4 every step equals a sequential replay of the processed events with conditions recomputed
//      on data where every earlier sequential action is applied, and the final snapshot and data
//      equal the replay's;
//   I5 background actions never block users;
//   I6 every engine reply validates (the reference validator checkReply of clients/go);
//   I7 no acknowledged update is lost.

import (
	"database/sql"
	"encoding/json"
	"fmt"
	"reflect"
	"sort"
	"strings"
	"time"

	"hostgo/refwrap"
)

type CheckResult struct {
	Name    string   `json:"name"`
	OK      bool     `json:"ok"`
	Cases   int      `json:"cases"`
	Fails   int      `json:"fails"`
	Samples []string `json:"samples,omitempty"`
}

func (c *CheckResult) fail(format string, args ...any) {
	c.Fails++
	c.OK = false
	if len(c.Samples) < 8 {
		c.Samples = append(c.Samples, fmt.Sprintf(format, args...))
	}
}

func newCheck(name string) *CheckResult { return &CheckResult{Name: name, OK: true} }

func canon(s string) any {
	if s == "" {
		return nil
	}
	return decodeJSON(s)
}

type wait struct {
	a string
	t int64
}

func pendingOf(snap any) map[wait]bool {
	m := map[wait]bool{}
	s, ok := snap.(map[string]any)
	if !ok {
		return m
	}
	ps, _ := s["pending"].([]any)
	for _, p := range ps {
		pm := p.(map[string]any)
		m[wait{pm["call"].(string), numOf(pm["token"])}] = true
	}
	return m
}

type logEntry struct {
	id                                       int64
	user, op, uid, event, data, guards, docv string
	token                                    sql.NullInt64
	hwm                                      sql.NullInt64
	pre, post                                sql.NullString
	actions                                  string
	recovered                                sql.NullBool
	waitUs                                   sql.NullInt64
}

func runChecks(st *Store, sims *Sims, ustats *UserStats, notifyLatency time.Duration) []*CheckResult {
	db := st.w
	var out []*CheckResult

	// ---- I1
	i1 := newCheck("I1 at most one irreversible effect per logical action / per cart")
	for _, s := range []*Sim{sims.crm, sims.loyalty, sims.booking, sims.notify, sims.audit} {
		s.mu.Lock()
		for tag, n := range s.effTag {
			i1.Cases++
			if n > 1 {
				i1.fail("%s: logical action %d (outbox row) executed %d times", s.name, tag, n)
			}
		}
		if s.name == "crm" {
			for cart, n := range s.effCart {
				i1.Cases++
				if n > 1 {
					i1.fail("crm: cart %s ordered %d times", cart, n)
				}
			}
		}
		s.mu.Unlock()
	}
	out = append(out, i1)

	// ---- load the step log
	rows, err := db.Query(`SELECT id, user, op, uid, coalesce(event,''), coalesce(data,'{}'), coalesce(guards,'{}'), coalesce(docv,''), token, hwm, pre, post, actions, recovered, wait_us FROM steplog ORDER BY id`)
	if err != nil {
		panic(err)
	}
	byUser := map[string][]logEntry{}
	var users []string
	for rows.Next() {
		var e logEntry
		if err := rows.Scan(&e.id, &e.user, &e.op, &e.uid, &e.event, &e.data, &e.guards, &e.docv, &e.token, &e.hwm, &e.pre, &e.post, &e.actions, &e.recovered, &e.waitUs); err != nil {
			panic(err)
		}
		if _, ok := byUser[e.user]; !ok {
			users = append(users, e.user)
		}
		byUser[e.user] = append(byUser[e.user], e)
	}
	rows.Close()
	sort.Strings(users)

	// ---- I2
	i2 := newCheck("I2 one outcome per call; one accepted outcome unless replaced/cleared")
	type callKey struct {
		user, action string
		token        int64
	}
	calls := map[callKey]string{} // → run
	rows, _ = db.Query(`SELECT user, name, token, run FROM outbox WHERE kind='call' AND token IS NOT NULL`)
	for rows.Next() {
		var k callKey
		var run string
		rows.Scan(&k.user, &k.action, &k.token, &run)
		if _, dup := calls[k]; dup {
			i2.fail("two calls with the same token: %v", k)
		}
		calls[k] = run
	}
	rows.Close()
	delivered := map[callKey]int{}
	rows, _ = db.Query(`SELECT user, action, token, run FROM outcome_log`)
	for rows.Next() {
		var k callKey
		var run string
		rows.Scan(&k.user, &k.action, &k.token, &run)
		if calls[k] != run {
			i2.fail("outcome with a run id of no call: %v %s", k, run)
		}
		delivered[k]++
	}
	rows.Close()
	for k := range calls {
		i2.Cases++
		if n := delivered[k]; n != 1 {
			i2.fail("call %v: %d outcome events delivered (want 1)", k, n)
		}
	}
	accepted := map[callKey]int{}
	cleared := map[callKey]string{}
	for _, u := range users {
		for _, e := range byUser[u] {
			pre := pendingOf(canon(e.pre.String))
			post := pendingOf(canon(e.post.String))
			var own wait
			isOutcome := strings.HasPrefix(e.event, "call:") && e.token.Valid
			if isOutcome {
				rest := strings.TrimPrefix(e.event, "call:")
				own = wait{rest[:strings.LastIndex(rest, ":")], e.token.Int64}
			}
			for w := range pre {
				if post[w] {
					continue
				}
				k := callKey{u, w.a, w.t}
				if isOutcome && w == own {
					accepted[k]++
				} else {
					cleared[k] = e.op + " " + e.event
				}
			}
			if isOutcome && pre[own] && post[own] {
				i2.fail("%s: outcome %s/%d not accepted although its wait was pending", u, e.event, e.token.Int64)
			}
		}
	}
	for k := range calls {
		a := accepted[k]
		_, c := cleared[k]
		if a > 1 || (a == 0 && !c) || (a == 1 && c) {
			i2.fail("call %v: accepted %d times, cleared=%v", k, a, c)
		}
	}
	out = append(out, i2)

	// ---- I3
	i3 := newCheck("I3 no stuck users")
	count := func(q string) int {
		var n int
		db.QueryRow(q).Scan(&n)
		return n
	}
	if n := count(`SELECT count(*) FROM inbound`); n != 0 {
		i3.fail("%d events left in the inbound queue", n)
	}
	if n := count(`SELECT count(*) FROM outbox WHERE state=0`); n != 0 {
		i3.fail("%d actions left unexecuted in the outbox", n)
	}
	if n := count(`SELECT count(*) FROM outbox WHERE state=2 AND detached=0`); n != 0 {
		i3.fail("%d sequential actions in the failure queue (their users wait for a decision)", n)
	}
	if n := count(`SELECT count(*) FROM outbox o WHERE state=2 AND detached=1 AND EXISTS(SELECT 1 FROM outbox p WHERE p.user=o.user AND p.state=0)`); n != 0 {
		i3.fail("%d background failures with blocked users", n)
	}
	rows, _ = db.Query(`SELECT user, snap FROM users`)
	finalSnap := map[string]sql.NullString{}
	for rows.Next() {
		var u string
		var s sql.NullString
		rows.Scan(&u, &s)
		finalSnap[u] = s
		i3.Cases++
		if !s.Valid {
			continue
		}
		snap := canon(s.String).(map[string]any)
		if p, ok := snap["pending"]; ok && len(p.([]any)) > 0 {
			i3.fail("%s: waits left in the snapshot: %v", u, p)
		}
		at := snap["at"].(string)
		if at == "placing" || at == "placing2" || at == "hold" {
			if len(pendingOf(snap)) == 0 {
				i3.fail("%s: on %s ('please wait') without a wait", u, at)
			}
		}
	}
	rows.Close()
	if ustats != nil && ustats.noReplyWait.Load() > 0 { // informational: an overloaded host is slow, not stuck
		i3.Samples = append(i3.Samples, fmt.Sprintf("info: %d times a simulated user on a waiting message waited longer than the reply wait", ustats.noReplyWait.Load()))
	}
	out = append(out, i3)

	// ---- I4 replay
	i4 := newCheck("I4 sequential replay (conditions on sequentially applied data) = host")
	bots := map[string]*refwrap.Bot{}
	defer func() {
		for _, b := range bots {
			b.Close()
		}
	}()
	botFor := func(key string) *refwrap.Bot {
		if b := bots[key]; b != nil {
			return b
		}
		b, err := refwrap.Load(string(docText(key)))
		if err != nil {
			panic(err)
		}
		bots[key] = b
		return b
	}
	dbKV := map[string]map[string]int64{}
	rows, _ = db.Query(`SELECT user, k, v FROM kv`)
	for rows.Next() {
		var u, k string
		var v int64
		rows.Scan(&u, &k, &v)
		if dbKV[u] == nil {
			dbKV[u] = map[string]int64{}
		}
		dbKV[u][k] = v
	}
	rows.Close()
	for _, u := range users {
		i4.Cases++
		var cur any
		kv := map[string]int64{}
		var hwm int64
		bad := false
		for _, e := range byUser[u] {
			if bad {
				break
			}
			if !reflect.DeepEqual(canon(e.pre.String), cur) {
				i4.fail("%s step %d (%s %s): stored snapshot before the step %s, replay %s", u, e.id, e.op, e.event, e.pre.String, mustJSON(cur))
				bad = true
				break
			}
			if e.op == "lose" {
				cur = nil
				continue
			}
			data := map[string]any{}
			if e.data != "" && e.data != "null" {
				data = decodeJSON(e.data).(map[string]any)
			}
			req := map[string]any{}
			switch e.op {
			case "start":
				req["op"] = "start"
				if data["support"] == "reset" {
					req["seq"] = hwm
				} else {
					req["event"] = e.event
					if hwm > 0 {
						req["seq"] = hwm
					}
				}
				if e.hwm.Int64 != hwm {
					i4.fail("%s step %d: start with high-water mark %d, replay %d", u, e.id, e.hwm.Int64, hwm)
				}
			case "restart":
				req["op"] = "restart"
				req["snapshot"] = cur
			case "step":
				req["op"] = "step"
				req["snapshot"] = cur
				req["event"] = e.event
				if e.token.Valid {
					req["token"] = e.token.Int64
				}
			}
			bot := botFor(e.docv)
			var resp map[string]any
			g := map[string]bool{}
			for round := 0; ; round++ {
				if len(g) > 0 {
					req["guards"] = g
				}
				r, v, err := bot.Request(req)
				if err != nil || round > 20 {
					i4.fail("%s step %d: replay request failed: %v %v", u, e.id, v, err)
					bad = true
					break
				}
				if v == "need" {
					for _, n := range r["need"].([]any) {
						g[n.(string)] = guardValue(n.(string), kv, data)
					}
					continue
				}
				if v != "result" {
					i4.fail("%s step %d: replay reply %v", u, e.id, r)
					bad = true
					break
				}
				resp = r
				break
			}
			if bad {
				break
			}
			_, rec := resp["recovered"]
			if !reflect.DeepEqual(resp["snapshot"], canon(e.post.String)) || !reflect.DeepEqual(resp["actions"], canon(e.actions)) ||
				rec != (e.recovered.Valid && e.recovered.Bool) {
				i4.fail("%s step %d (%s %s, docv %s): host %s %s, replay %s %s (host guards %s, replay guards %s)", u, e.id, e.op, e.event,
					e.docv, e.post.String, e.actions, mustJSON(resp["snapshot"]), mustJSON(resp["actions"]), e.guards, mustJSON(g))
				bad = true
				break
			}
			cur = resp["snapshot"]
			if s := numOf(cur.(map[string]any)["seq"]); s > hwm {
				hwm = s
			}
			for _, a0 := range resp["actions"].([]any) {
				a := a0.(map[string]any)
				if c, ok := a["call"].(string); ok && ownDB[c] {
					applyEffect(kv, c, data)
				}
			}
		}
		if bad {
			continue
		}
		fs := finalSnap[u]
		var final any
		if fs.Valid {
			final = canon(fs.String)
		}
		if !reflect.DeepEqual(final, cur) {
			i4.fail("%s: final snapshot %s, replay %s", u, fs.String, mustJSON(cur))
			continue
		}
		dk := dbKV[u]
		for k, v := range kv {
			if dk[k] != v {
				i4.fail("%s: data %s = %d in the store, %d in the replay", u, k, dk[k], v)
				break
			}
		}
		for k, v := range dk {
			if kv[k] != v {
				i4.fail("%s: data %s = %d in the store, %d in the replay", u, k, v, kv[k])
				break
			}
		}
	}
	out = append(out, i4)

	// ---- I5: users whose background calls are slow (or fail for good) must not wait more often
	// at the barrier than the others
	i5 := newCheck("I5 background actions never block users")
	limit := notifyLatency.Microseconds()
	// statistics over the steps that follow a step which emitted a background action
	var slowN, slowHit, othN, othHit int
	var waits []int64
	for _, u := range users {
		slow := sims.notify.slow[u]
		prevBG := false
		for _, e := range byUser[u] {
			if e.waitUs.Valid {
				waits = append(waits, e.waitUs.Int64)
				if prevBG {
					hit := e.waitUs.Int64 >= limit
					if slow {
						slowN++
						if hit {
							slowHit++
						}
					} else {
						othN++
						if hit {
							othHit++
						}
					}
				}
			}
			prevBG = strings.Contains(e.actions, `"detached":true`)
		}
	}
	sort.Slice(waits, func(i, j int) bool { return waits[i] < waits[j] })
	i5.Cases = len(waits)
	if n := count(`SELECT count(*) FROM outbox o WHERE o.detached=1 AND o.state=2 AND EXISTS(SELECT 1 FROM outbox p WHERE p.user=o.user AND p.state<>1 AND p.detached=0)`); n != 0 {
		i5.fail("%d failed background actions with users whose queue is stopped", n)
	}
	if n := count(`SELECT count(*) FROM failq f JOIN outbox o ON o.id=f.outbox_id WHERE o.detached=1 AND o.state=2 AND o.user IN (SELECT user FROM outbox WHERE state=2 AND detached=0)`); n != 0 {
		i5.fail("%d background failures in a stopped sequential queue", n)
	}
	fs, fo := 0.0, 0.0
	if slowN > 0 {
		fs = float64(slowHit) / float64(slowN)
	}
	if othN > 0 {
		fo = float64(othHit) / float64(othN)
	}
	if slowN > 0 && fs > 2*fo+0.02 {
		i5.fail("after a step with a background action, users whose background calls are slow wait ≥ %d µs at the barrier in %.1f%% of steps, the others in %.1f%%", limit, 100*fs, 100*fo)
	}
	if len(waits) > 0 {
		i5.Samples = append(i5.Samples, fmt.Sprintf("barrier wait p50 %d µs, p99 %d µs, max %d µs; after a background action, waits ≥ %d µs: slow-background users %.2f%% of %d steps, others %.2f%% of %d",
			waits[len(waits)/2], waits[len(waits)*99/100], waits[len(waits)-1], limit, 100*fs, slowN, 100*fo, othN))
	}
	out = append(out, i5)

	// ---- I6
	i6 := newCheck("I6 every reply validates (checkReply of clients/go)")
	i6.Cases = int(count(`SELECT count(*) FROM steplog`))
	if n := protoViolations.Load(); n != 0 {
		i6.fail("%d protocol violations, e.g. %v", n, protoViolationSample.Load())
	}
	out = append(out, i6)

	// ---- I7
	i7 := newCheck("I7 no acknowledged update lost")
	rows, _ = db.Query(`SELECT DISTINCT i.user, i.uid FROM ingest_log i WHERE NOT EXISTS (SELECT 1 FROM processed p WHERE p.user=i.user AND p.uid=i.uid)`)
	for rows.Next() {
		var u, uid string
		rows.Scan(&u, &uid)
		i7.fail("%s: update %s acknowledged but never processed", u, uid)
	}
	rows.Close()
	i7.Cases = count(`SELECT count(DISTINCT user||uid) FROM ingest_log`)
	if n := count(`SELECT count(*) FROM drops WHERE reason LIKE 'engine error%'`); n != 0 {
		var r string
		db.QueryRow(`SELECT reason FROM drops WHERE reason LIKE 'engine error%' LIMIT 1`).Scan(&r)
		i7.fail("%d events failed in the engine (failure queue), e.g. %s", n, r)
	}
	out = append(out, i7)
	return out
}

func summarize(res []*CheckResult) (string, bool) {
	ok := true
	var b strings.Builder
	for _, r := range res {
		st := "PASS"
		if !r.OK {
			st = "FAIL"
			ok = false
		}
		fmt.Fprintf(&b, "%s %s (cases %d, fails %d)\n", st, r.Name, r.Cases, r.Fails)
		for _, s := range r.Samples {
			fmt.Fprintf(&b, "    %s\n", s)
		}
	}
	return b.String(), ok
}

var _ = json.Marshal

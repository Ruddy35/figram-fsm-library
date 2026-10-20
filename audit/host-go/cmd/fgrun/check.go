package main

// Invariant checker. Reads the host DB (read-only), the external simulator state and
// the runner's ack log. It does NOT reuse the host's guard/domain code: guards and the
// final domain are re-derived from an independent model that replays the committed
// transitions (actions come from the engine) in commit order.

import (
	"database/sql"
	"encoding/json"
	"fmt"
	"reflect"
	"regexp"
	"sort"
	"strings"
)

var (
	zonesM = map[string]bool{"Lenina": true, "Mira": true, "Pushkina": true, "Gagarina": true}
	slotsM = map[string]int{"s1": 1, "s2": 2, "s3": 4, "s4": 10}
	extOps = map[string]string{"create_order": "create_order", "notify_calendar": "calendar", "create_ticket": "ticket_create",
		"log_message": "ticket_message", "escalate": "ticket_escalate", "close_ticket": "ticket_close", "notify_owner": "notify"}
	streetRe = regexp.MustCompile(`^[\s,]*([^\s,]+)`)
)

type Trans struct {
	N         int64             `json:"-"`
	User      string            `json:"-"`
	UID       string            `json:"update_id"`
	Event     string            `json:"event"`
	Arg       *string           `json:"arg"`
	Token     *int64            `json:"token"`
	Kind      string            `json:"kind"`
	SeqBefore int64             `json:"-"`
	Seq       int64             `json:"seq"`
	At        string            `json:"at"`
	Doc       string            `json:"-"`
	TMs       int64             `json:"t_ms"`
	Actions   []map[string]any  `json:"actions"`
	Recovered bool              `json:"recovered"`
	Guards    map[string]bool   `json:"-"`
	Changed   bool              `json:"-"`
	Note      string            `json:"-"`
}

type outboxRow struct {
	Key, User, Kind, Name, State string
	Param                        sql.NullString
}

type DBState struct {
	Users     map[string]map[string]any // user -> {snapshot(any), doc, hw}
	SnapText  map[string]sql.NullString
	HW        map[string]int64
	Trans     []Trans
	Outbox    map[string]outboxRow
	Processed map[string]string // user|uid -> status
	Timers    [][]any
	Domain    map[string][][]any
	Rejected  int
	Log       map[string]int
	FaultLog  map[string]map[string]int // engine_fault_injected/detected -> mode -> n
}

func openRO(path string) (*sql.DB, error) {
	return sql.Open("sqlite", "file:"+path+"?mode=ro&_pragma=busy_timeout(30000)")
}

func qrows(db *sql.DB, q string, args ...any) [][]any {
	rows, err := db.Query(q, args...)
	if err != nil {
		panic(fmt.Sprintf("%s: %v", q, err))
	}
	defer rows.Close()
	cols, _ := rows.Columns()
	var out [][]any
	for rows.Next() {
		vals := make([]any, len(cols))
		ptrs := make([]any, len(cols))
		for i := range vals {
			ptrs[i] = &vals[i]
		}
		rows.Scan(ptrs...)
		for i, v := range vals {
			if b, ok := v.([]byte); ok {
				vals[i] = string(b)
			}
		}
		out = append(out, vals)
	}
	return out
}

var domainQ = [][2]string{
	{"prefs", "SELECT user,size FROM prefs ORDER BY user"},
	{"cart", "SELECT user,size,key FROM cart ORDER BY user,key"},
	{"addresses", "SELECT user,address FROM addresses ORDER BY user"},
	{"orders_local", "SELECT key,user,items,address FROM orders_local ORDER BY key"},
	{"selection", "SELECT user,slot FROM selection ORDER BY user"},
	{"reservations", "SELECT key,user,slot,active FROM reservations ORDER BY key"},
	{"calendar_local", "SELECT key,user FROM calendar_local ORDER BY key"},
	{"tickets", "SELECT user,state,messages FROM tickets ORDER BY user"},
	{"reminders", "SELECT user,text FROM reminders ORDER BY user"},
	{"pings", "SELECT key,user,text FROM pings ORDER BY key"},
	{"notifications_local", "SELECT key,user FROM notifications_local ORDER BY key"},
}

func loadState(path string) (*DBState, error) {
	db, err := openRO(path)
	if err != nil {
		return nil, err
	}
	defer db.Close()
	s := &DBState{Users: map[string]map[string]any{}, SnapText: map[string]sql.NullString{}, HW: map[string]int64{},
		Outbox: map[string]outboxRow{}, Processed: map[string]string{}, Domain: map[string][][]any{}, Log: map[string]int{}}
	rows, err := db.Query(`SELECT user,doc,snapshot,hw_seq FROM users ORDER BY user`)
	if err != nil {
		return nil, err
	}
	for rows.Next() {
		var u, doc string
		var snap sql.NullString
		var hw int64
		rows.Scan(&u, &doc, &snap, &hw)
		var sv any
		if snap.Valid {
			if json.Unmarshal([]byte(snap.String), &sv) != nil {
				sv = map[string]any{"_unparseable": snap.String}
			}
		}
		s.Users[u] = map[string]any{"snapshot": sv, "doc": doc}
		s.SnapText[u] = snap
		s.HW[u] = hw
	}
	rows.Close()
	rows, err = db.Query(`SELECT n,user,update_id,event,arg,token,kind,seq_before,seq,at,doc,actions,recovered,t_ms,guards,changed,note
		FROM transitions ORDER BY n`)
	if err != nil {
		return nil, err
	}
	for rows.Next() {
		var t Trans
		var uid, ev, at, doc, acts, guards, note sql.NullString
		var arg sql.NullString
		var tok, sb, seq, tms sql.NullInt64
		var rec, ch int
		if err := rows.Scan(&t.N, &t.User, &uid, &ev, &arg, &tok, &t.Kind, &sb, &seq, &at, &doc, &acts, &rec, &tms, &guards, &ch, &note); err != nil {
			return nil, err
		}
		t.UID, t.Event, t.At, t.Doc, t.Note = uid.String, ev.String, at.String, doc.String, note.String
		if arg.Valid {
			a := arg.String
			t.Arg = &a
		}
		if tok.Valid {
			x := tok.Int64
			t.Token = &x
		}
		t.SeqBefore, t.Seq, t.TMs = sb.Int64, seq.Int64, tms.Int64
		t.Recovered, t.Changed = rec == 1, ch == 1
		t.Actions = []map[string]any{}
		if acts.Valid && acts.String != "" {
			dec := json.NewDecoder(strings.NewReader(acts.String))
			dec.UseNumber()
			dec.Decode(&t.Actions)
		}
		if guards.Valid {
			json.Unmarshal([]byte(guards.String), &t.Guards)
		}
		s.Trans = append(s.Trans, t)
	}
	rows.Close()
	rows, err = db.Query(`SELECT key,user,kind,name,state,param FROM outbox`)
	if err != nil {
		return nil, err
	}
	for rows.Next() {
		var o outboxRow
		rows.Scan(&o.Key, &o.User, &o.Kind, &o.Name, &o.State, &o.Param)
		s.Outbox[o.Key] = o
	}
	rows.Close()
	for _, r := range qrows(db, `SELECT user,update_id,status FROM processed`) {
		s.Processed[fmt.Sprint(r[0])+"|"+fmt.Sprint(r[1])] = fmt.Sprint(r[2])
	}
	s.Timers = qrows(db, `SELECT user,name,token,due_ms,state,delivered FROM timers ORDER BY id`)
	for _, q := range domainQ {
		s.Domain[q[0]] = qrows(db, q[1])
	}
	db.QueryRow(`SELECT COUNT(*) FROM rejected`).Scan(&s.Rejected)
	for _, r := range qrows(db, `SELECT kind, COUNT(*) FROM host_log GROUP BY kind`) {
		s.Log[fmt.Sprint(r[0])] = int(r[1].(int64))
	}
	s.FaultLog = map[string]map[string]int{"engine_fault_injected": {}, "engine_fault_detected": {}}
	for _, r := range qrows(db, `SELECT kind, detail FROM host_log WHERE kind LIKE 'engine_fault_%'`) {
		var d map[string]any
		json.Unmarshal([]byte(fmt.Sprint(r[1])), &d)
		s.FaultLog[fmt.Sprint(r[0])][fmt.Sprint(d["mode"])]++
	}
	return s, nil
}

// dumpForRestart: everything a restart must not change.
func dumpForRestart(path string, ext *ExtSim) map[string]any {
	db, err := openRO(path)
	if err != nil {
		return map[string]any{"error": err.Error()}
	}
	defer db.Close()
	d := map[string]any{
		"users":       qrows(db, `SELECT user,doc,snapshot,hw_seq FROM users ORDER BY user`),
		"outbox":      qrows(db, `SELECT key,state FROM outbox ORDER BY key`),
		"timers":      qrows(db, `SELECT user,name,token,due_ms,state,delivered FROM timers ORDER BY id`),
		"processed":   qrows(db, `SELECT COUNT(*) FROM processed`),
		"transitions": qrows(db, `SELECT COUNT(*) FROM transitions`),
	}
	dom := map[string]any{}
	for _, q := range domainQ {
		dom[q[0]] = qrows(db, q[1])
	}
	d["domain"] = dom
	ext.mu.Lock()
	d["ext"] = []int{len(ext.Requests), len(ext.TG), len(ext.Effects)}
	ext.mu.Unlock()
	return d
}

func streetOf(a *string) string {
	if a == nil {
		return ""
	}
	m := streetRe.FindStringSubmatch(*a)
	if m == nil {
		return ""
	}
	return m[1]
}

// ------------------------------------------------------------------ independent model

type model struct {
	cart    map[string][][2]string // user -> [(key,size)]
	prefs   map[string]string
	addr    map[string]string
	orders  map[string][2]any // key -> (items json, address)
	sel     map[string]string
	res     map[string]map[string]string // slot -> user -> key
	resKeys map[string][3]any            // key -> (user, slot, active)
	cal     map[string]string
	tickets map[string][2]any // user -> (state, messages)
	rem     map[string]string
	pings   map[string]*string
	notif   map[string]string
	viol    []string
}

func newModel() *model {
	return &model{cart: map[string][][2]string{}, prefs: map[string]string{}, addr: map[string]string{},
		orders: map[string][2]any{}, sel: map[string]string{}, res: map[string]map[string]string{}, resKeys: map[string][3]any{},
		cal: map[string]string{}, tickets: map[string][2]any{}, rem: map[string]string{}, pings: map[string]*string{}, notif: map[string]string{}}
}

func (m *model) guard(name, user string, arg *string) bool {
	switch name {
	case "cart_not_empty":
		return len(m.cart[user]) > 0
	case "in_delivery_zone":
		return zonesM[streetOf(arg)]
	case "slot_free":
		s, ok := m.sel[user]
		c, known := slotsM[s]
		_, mine := m.res[s][user]
		return ok && known && !mine && len(m.res[s]) < c
	case "has_booking":
		for _, us := range m.res {
			if _, ok := us[user]; ok {
				return true
			}
		}
		return false
	case "can_escalate":
		t, ok := m.tickets[user]
		return ok && t[0] == "open"
	case "ticket_open":
		t, ok := m.tickets[user]
		return ok && (t[0] == "open" || t[0] == "escalated")
	case "has_reminder":
		return m.rem[user] != ""
	case "nag_enabled":
		return strings.HasPrefix(m.rem[user], "!")
	}
	return false
}

func (m *model) apply(user, call, key string, arg *string) {
	a := ""
	if arg != nil {
		a = *arg
	}
	switch call {
	case "clear_cart":
		delete(m.cart, user)
	case "save_size":
		if a == "" {
			a = "M"
		}
		m.prefs[user] = a
	case "add_to_cart":
		size := m.prefs[user]
		if size == "" {
			size = "M"
		}
		m.cart[user] = append(m.cart[user], [2]string{key, size})
	case "save_address":
		m.addr[user] = a
	case "create_order":
		c := append([][2]string{}, m.cart[user]...)
		sort.Slice(c, func(i, j int) bool { return c[i][0] < c[j][0] })
		items := []string{}
		for _, x := range c {
			items = append(items, x[1])
		}
		b, _ := json.Marshal(items)
		var ad any
		if v, ok := m.addr[user]; ok {
			ad = v
		}
		m.orders[key] = [2]any{string(b), ad}
		delete(m.cart, user)
	case "choose_slot":
		m.sel[user] = a
	case "reserve_slot":
		s, ok := m.sel[user]
		c := slotsM[s]
		if _, mine := m.res[s][user]; !ok || mine || len(m.res[s]) >= c {
			m.viol = append(m.viol, fmt.Sprintf("reserve_slot %s by %s would fail (slot %q)", key, user, s))
			return
		}
		if m.res[s] == nil {
			m.res[s] = map[string]string{}
		}
		m.res[s][user] = key
		m.resKeys[key] = [3]any{user, s, int64(1)}
	case "notify_calendar":
		m.cal[key] = user
	case "release_hold":
		delete(m.sel, user)
	case "cancel_booking":
		for s, us := range m.res {
			if k, ok := us[user]; ok {
				delete(us, user)
				m.resKeys[k] = [3]any{user, s, int64(0)}
			}
		}
	case "create_ticket":
		m.tickets[user] = [2]any{"open", int64(1)}
	case "log_message":
		if t, ok := m.tickets[user]; ok {
			m.tickets[user] = [2]any{t[0], t[1].(int64) + 1}
		}
	case "escalate":
		if t, ok := m.tickets[user]; ok {
			m.tickets[user] = [2]any{"escalated", t[1]}
		}
	case "close_ticket":
		if t, ok := m.tickets[user]; ok {
			m.tickets[user] = [2]any{"closed", t[1]}
		}
	case "save_reminder":
		if a != "" {
			m.rem[user] = a
		} else {
			delete(m.rem, user)
		}
	case "clear_reminder":
		delete(m.rem, user)
	case "log_ping":
		if t, ok := m.rem[user]; ok {
			m.pings[key] = &t
		} else {
			m.pings[key] = nil
		}
	case "notify_owner":
		m.notif[key] = user
	}
}

func (m *model) reset(family, user string) {
	switch family {
	case "pizza":
		delete(m.cart, user)
		delete(m.prefs, user)
		delete(m.addr, user)
	case "booking":
		delete(m.sel, user)
	case "reminders":
		delete(m.rem, user)
	}
}

func (m *model) domain() map[string][][]any {
	d := map[string][][]any{}
	add := func(t string, row ...any) { d[t] = append(d[t], row) }
	for _, u := range sortedKeys(m.prefs) {
		add("prefs", u, m.prefs[u])
	}
	var cart [][3]string
	for u, items := range m.cart {
		for _, it := range items {
			cart = append(cart, [3]string{u, it[1], it[0]})
		}
	}
	sort.Slice(cart, func(i, j int) bool {
		if cart[i][0] != cart[j][0] {
			return cart[i][0] < cart[j][0]
		}
		return cart[i][2] < cart[j][2]
	})
	for _, c := range cart {
		add("cart", c[0], c[1], c[2])
	}
	for _, u := range sortedKeys(m.addr) {
		add("addresses", u, m.addr[u])
	}
	for _, k := range sortedKeys(m.orders) {
		add("orders_local", k, strings.SplitN(k, ":", 2)[0], m.orders[k][0], m.orders[k][1])
	}
	for _, u := range sortedKeys(m.sel) {
		add("selection", u, m.sel[u])
	}
	for _, k := range sortedKeys(m.resKeys) {
		r := m.resKeys[k]
		add("reservations", k, r[0], r[1], r[2])
	}
	for _, k := range sortedKeys(m.cal) {
		add("calendar_local", k, m.cal[k])
	}
	for _, u := range sortedKeys(m.tickets) {
		add("tickets", u, m.tickets[u][0], m.tickets[u][1])
	}
	for _, u := range sortedKeys(m.rem) {
		add("reminders", u, m.rem[u])
	}
	for _, k := range sortedKeys(m.pings) {
		var t any
		if m.pings[k] != nil {
			t = *m.pings[k]
		}
		add("pings", k, strings.SplitN(k, ":", 2)[0], t)
	}
	for _, k := range sortedKeys(m.notif) {
		add("notifications_local", k, m.notif[k])
	}
	return d
}

func actKind(a map[string]any) (string, string) {
	for _, k := range []string{"send", "call", "start_timer", "cancel_timer"} {
		if v, ok := a[k]; ok {
			s, _ := v.(string)
			return k, s
		}
	}
	return "", ""
}

func numOf(v any) int64 {
	switch x := v.(type) {
	case json.Number:
		n, _ := x.Int64()
		return n
	case float64:
		return int64(x)
	case int64:
		return x
	}
	return 0
}

func normRow(r []any) []any {
	out := make([]any, len(r))
	for i, v := range r {
		switch x := v.(type) {
		case int64:
			out[i] = x
		case int:
			out[i] = int64(x)
		case float64:
			out[i] = int64(x)
		default:
			out[i] = v
		}
	}
	return out
}

// ------------------------------------------------------------------ invariants

type checkCtx struct {
	r       *Runner
	st      *DBState
	scen    string
	family  string
	inv     map[string]string
	fails   map[string][]string
	clean   *DBState
	pristine *DBState
	before  map[string]any
	after   map[string]any
}

func (c *checkCtx) set(name string, bad []string) {
	if len(bad) == 0 {
		c.inv[name] = "PASS"
		return
	}
	c.inv[name] = "FAIL"
	if len(bad) > 12 {
		bad = append(bad[:12], fmt.Sprintf("... %d more", len(bad)-12))
	}
	c.fails[name] = bad
}

func changedByUser(st *DBState) map[string][]Trans {
	out := map[string][]Trans{}
	for _, t := range st.Trans {
		if t.Changed {
			out[t.User] = append(out[t.User], t)
		}
	}
	return out
}

func (c *checkCtx) run() {
	st := c.st
	ext := c.r.ext
	byUser := changedByUser(st)

	// action keys of committed transitions
	type actRef struct {
		user, kind, name string
		t           Trans
	}
	acts := map[string][]actRef{}
	for _, t := range st.Trans {
		if !t.Changed {
			continue
		}
		for i, a := range t.Actions {
			k, n := actKind(a)
			key := fmt.Sprintf("%s:%d:%d", t.User, t.Seq, i)
			acts[key] = append(acts[key], actRef{t.User, k, n, t})
		}
	}

	// idempotency_keys_unique_lifetime
	var bad []string
	for k, v := range acts {
		if len(v) > 1 {
			bad = append(bad, fmt.Sprintf("key %s used by %d transitions", k, len(v)))
		}
	}
	ext.mu.Lock()
	for _, cf := range ext.Conflicts {
		bad = append(bad, fmt.Sprintf("CRM key conflict %v", cf))
	}
	effects := map[string]*crmEffect{}
	for k, e := range ext.Effects {
		effects[k] = e
	}
	tgBy := map[string]int{}
	for _, d := range ext.TG {
		tgBy[d.Key]++
	}
	reqModes := map[string][]string{}
	for _, rq := range ext.Requests {
		reqModes[rq.Key] = append(reqModes[rq.Key], rq.Mode)
	}
	ext.mu.Unlock()
	sort.Strings(bad)
	c.set("idempotency_keys_unique_lifetime", bad)

	// seq_plus_one
	bad = nil
	for _, u := range sortedKeys(byUser) {
		prev := int64(0)
		for i, t := range byUser[u] {
			if i == 0 && t.Kind != "start" {
				bad = append(bad, fmt.Sprintf("%s first transition is %s", u, t.Kind))
			}
			if t.Seq != prev+1 {
				bad = append(bad, fmt.Sprintf("%s %s seq %d after %d", u, t.UID, t.Seq, prev))
			}
			prev = t.Seq
		}
		if hw, ok := st.HW[u]; ok && hw != prev {
			bad = append(bad, fmt.Sprintf("%s hw_seq %d != last seq %d", u, hw, prev))
		}
	}
	c.set("seq_plus_one", bad)

	// one_order_per_payment
	bad = nil
	payments := map[string]map[string]bool{}
	for _, t := range st.Trans {
		if !t.Changed || t.Event != "payment:success" {
			continue
		}
		for _, a := range t.Actions {
			if _, n := actKind(a); n == "create_order" {
				if payments[t.User] == nil {
					payments[t.User] = map[string]bool{}
				}
				payments[t.User][t.UID] = true
			}
		}
	}
	ordersByUser := map[string]int{}
	for _, e := range effects {
		if e.Op == "create_order" {
			ordersByUser[e.User]++
		}
	}
	localOrders := map[string]int{}
	for _, r := range st.Domain["orders_local"] {
		localOrders[fmt.Sprint(r[1])]++
	}
	users := map[string]bool{}
	for u := range payments {
		users[u] = true
	}
	for u := range ordersByUser {
		users[u] = true
	}
	for _, u := range sortedKeys(users) {
		if ordersByUser[u] != len(payments[u]) || localOrders[u] != len(payments[u]) {
			bad = append(bad, fmt.Sprintf("%s: %d paid updates, %d CRM orders, %d local orders", u, len(payments[u]), ordersByUser[u], localOrders[u]))
		}
	}
	c.set("one_order_per_payment", bad)

	// external_effect_once_per_intent
	bad = nil
	intents := map[string]bool{}
	for k, v := range acts {
		if v[0].kind == "call" {
			if _, ext := extOps[v[0].name]; ext {
				intents[k] = true
			}
		}
	}
	for _, k := range sortedKeys(intents) {
		if effects[k] == nil {
			bad = append(bad, "intent without CRM effect "+k)
		}
	}
	for _, k := range sortedKeys(effects) {
		if !intents[k] {
			bad = append(bad, "CRM effect without intent "+k)
		}
	}
	c.set("external_effect_once_per_intent", bad)

	// actions_complete / send_at_least_once
	bad = nil
	var badSend []string
	for _, k := range sortedKeys(acts) {
		a := acts[k][0]
		if a.kind != "call" && a.kind != "send" {
			continue
		}
		o, ok := st.Outbox[k]
		if !ok {
			bad = append(bad, "no outbox row for "+k)
			if a.kind == "send" {
				badSend = append(badSend, "never delivered "+k)
			}
			continue
		}
		if o.State != "done" {
			bad = append(bad, fmt.Sprintf("outbox %s is %s", k, o.State))
		}
		if a.kind == "send" && tgBy[k] == 0 {
			badSend = append(badSend, "never delivered "+k)
		}
	}
	c.set("actions_complete", bad)
	c.set("send_at_least_once", badSend)

	// send_duplicates_explained: a key delivered more than once must have seen a
	// fault on one of its requests (timeout/dup/delay) or the host process died
	// between its first and its last delivery (kill -9 between effect and mark).
	bad = nil
	ext.mu.Lock()
	tgTimes := map[string][]int64{}
	for _, d := range ext.TG {
		tgTimes[d.Key] = append(tgTimes[d.Key], d.TUs)
	}
	ext.mu.Unlock()
	var deaths []int64
	for _, e := range c.r.Exits {
		if e.Gen >= 0 && (e.Signal != "" || (!e.Expected && e.Code != 0)) {
			deaths = append(deaths, e.TUs) // any abrupt end of a host process
		}
	}
	for _, k := range sortedKeys(tgBy) {
		if tgBy[k] <= 1 {
			continue
		}
		explained := false
		for _, m := range reqModes[k] {
			if m == "timeout" || m == "dup" || m == "dup2" || m == "delay" {
				explained = true
			}
		}
		ts := tgTimes[k]
		for _, d := range deaths {
			if d > ts[0] && d <= ts[len(ts)-1] {
				explained = true
			}
		}
		if !explained {
			bad = append(bad, fmt.Sprintf("%s delivered %d times without a fault on its requests or a host death in between", k, tgBy[k]))
		}
	}
	c.set("send_duplicates_explained", bad)

	// outbox_drained
	bad = nil
	for _, k := range sortedKeys(st.Outbox) {
		if o := st.Outbox[k]; o.State != "done" {
			bad = append(bad, fmt.Sprintf("%s %s %s", k, o.Name, o.State))
		}
	}
	c.set("outbox_drained", bad)

	// no_lost_ack / all_updates_acked
	bad = nil
	for _, a := range c.r.Acks {
		if a.Raw || a.Status == "rejected" {
			continue
		}
		if _, ok := st.Processed[a.User+"|"+a.UID]; !ok {
			bad = append(bad, fmt.Sprintf("acked %s %s (%s) not in processed", a.User, a.UID, a.Status))
		}
	}
	c.set("no_lost_ack", bad)
	bad = nil
	for _, a := range c.r.Abandoned {
		bad = append(bad, fmt.Sprintf("abandoned line %v %v %v: %v", a["line"], a["user"], a["uid"], a["why"]))
	}
	c.set("all_updates_acked", bad)

	// model replay: guards, domain, timers
	m := newModel()
	type tstate struct{ token, start, ms int64 }
	active := map[string]map[string]tstate{}
	var badG, badStale, badEarly []string
	for _, t := range st.Trans {
		if t.Kind == "reset" {
			m.reset(c.family, t.User)
		}
		if t.Kind == "reset" || t.Kind == "repair" || t.Recovered {
			delete(active, t.User)
		}
		if t.Guards != nil {
			for _, g := range sortedKeys(t.Guards) {
				want := m.guard(g, t.User, t.Arg)
				if want != t.Guards[g] {
					badG = append(badG, fmt.Sprintf("%s seq %d %s %s: recorded %v, model %v", t.User, t.Seq, t.Event, g, t.Guards[g], want))
				}
			}
		}
		if !t.Changed {
			continue
		}
		if strings.HasPrefix(t.Event, "timer:") && t.Token != nil {
			name := t.Event[6:]
			cur, ok := active[t.User][name]
			if !ok || cur.token != *t.Token {
				badStale = append(badStale, fmt.Sprintf("%s %s token %d accepted, active %+v", t.User, t.Event, *t.Token, cur))
			} else if t.TMs < cur.start+cur.ms {
				badEarly = append(badEarly, fmt.Sprintf("%s %s fired at %d < %d", t.User, t.Event, t.TMs, cur.start+cur.ms))
			}
		}
		for i, a := range t.Actions {
			k, n := actKind(a)
			key := fmt.Sprintf("%s:%d:%d", t.User, t.Seq, i)
			switch k {
			case "call":
				m.apply(t.User, n, key, t.Arg)
			case "cancel_timer":
				delete(active[t.User], n)
			case "start_timer":
				if active[t.User] == nil {
					active[t.User] = map[string]tstate{}
				}
				active[t.User][n] = tstate{numOf(a["token"]), t.TMs, numOf(a["ms"])}
			}
		}
	}
	c.set("guards_match_domain_model", badG)
	c.set("stale_timer_no_effect", badStale)
	c.set("timer_not_early", badEarly)
	bad = append([]string{}, m.viol...)
	md := m.domain()
	for _, q := range domainQ {
		var got, want [][]any
		for _, r := range st.Domain[q[0]] {
			got = append(got, normRow(r))
		}
		for _, r := range md[q[0]] {
			want = append(want, normRow(r))
		}
		if !reflect.DeepEqual(fmt.Sprint(got), fmt.Sprint(want)) {
			bad = append(bad, fmt.Sprintf("%s: db %v != model %v", q[0], trunc(fmt.Sprint(got), 300), trunc(fmt.Sprint(want), 300)))
		}
	}
	c.set("domain_model_consistent", bad)

	// no_overbooking
	bad = nil
	act := map[string]int{}
	pairs := map[string]int{}
	for _, r := range st.Domain["reservations"] {
		if numOf(r[3]) == 1 {
			act[fmt.Sprint(r[2])]++
			pairs[fmt.Sprint(r[1], "|", r[2])]++
		}
	}
	for s, n := range act {
		if n > slotsM[s] {
			bad = append(bad, fmt.Sprintf("slot %s: %d active > capacity %d", s, n, slotsM[s]))
		}
	}
	for p, n := range pairs {
		if n > 1 {
			bad = append(bad, fmt.Sprintf("%s holds %d reservations", p, n))
		}
	}
	c.set("no_overbooking", bad)

	// snapshots_valid_final: every stored snapshot parses and seq == hw
	bad = nil
	for _, u := range sortedKeys(st.SnapText) {
		s := st.SnapText[u]
		var obj map[string]any
		if !s.Valid || json.Unmarshal([]byte(s.String), &obj) != nil {
			bad = append(bad, u+": unreadable snapshot")
			continue
		}
		if numOf(obj["seq"]) != st.HW[u] {
			bad = append(bad, fmt.Sprintf("%s: snapshot seq %v != hw %d", u, obj["seq"], st.HW[u]))
		}
	}
	c.set("snapshots_valid_final", bad)

	// crashes_effective: every armed crash point really killed the host (rc -9)
	bad = nil
	armed := 0
	engineKills := 0
	for _, f := range c.r.Faults {
		if f["fault"] == "crash" {
			armed++
		}
		if f["fault"] == "engine_fault" && f["mode"] == "kill" {
			engineKills++
		}
	}
	killed := 0
	for _, e := range c.r.Exits {
		if e.Signal == "killed" {
			killed++
		}
	}
	if armed+engineKills > 0 {
		if len(c.r.Fired) != armed+engineKills || killed != armed+engineKills {
			bad = append(bad, fmt.Sprintf("armed %d crash points + %d engine kills, fired %d, SIGKILL exits %d", armed, engineKills, len(c.r.Fired), killed))
		}
	}
	c.set("crashes_effective", bad)

	// no_unexpected_host_death: the host process only ever died by our own kill -9
	bad = nil
	for _, e := range c.r.Exits {
		if e.Gen < 0 {
			continue // startup attempt while the DB was locked (expected retry)
		}
		if !e.Expected && e.Signal != "killed" {
			bad = append(bad, fmt.Sprintf("host gen %d pid %d died: signal=%q code=%d", e.Gen, e.Pid, e.Signal, e.Code))
		}
		if e.Expected && e.Signal != "" && e.Signal != "killed" {
			bad = append(bad, fmt.Sprintf("host gen %d pid %d died during stop: signal=%q", e.Gen, e.Pid, e.Signal))
		}
	}
	c.set("no_unexpected_host_death", bad)

	// external_faults_effective
	bad = nil
	for _, mleft := range ext.ModesLeft() {
		bad = append(bad, fmt.Sprintf("line %d %s/%s: %d of %d not consumed", mleft.Line, mleft.Target, mleft.Mode, mleft.Remaining, mleft.Count))
	}
	hasReorder := false
	for _, f := range c.r.Faults {
		if f["fault"] == "external" && f["mode"] == "reorder" {
			hasReorder = true
		}
	}
	if hasReorder && len(ext.Reorders) == 0 {
		bad = append(bad, "reorder requested but no batch was reordered")
	}
	c.set("external_faults_effective", bad)

	// engine_faults_effective (FFI mapping): injected and detected by the host
	bad = nil
	want := map[string]int{}
	for _, f := range c.r.Faults {
		if f["fault"] == "engine_fault" {
			want[fmt.Sprint(f["mode"])]++
		}
	}
	if len(want) > 0 {
		inj := st.FaultLog["engine_fault_injected"]
		det := st.FaultLog["engine_fault_detected"]
		kills := 0
		for _, f := range c.r.Fired {
			if f.At == "engine_kill" {
				kills++
			}
		}
		for _, mode := range sortedKeys(want) {
			if mode == "kill" {
				if kills < want[mode] {
					bad = append(bad, fmt.Sprintf("engine kill: wanted %d, host died %d times", want[mode], kills))
				}
				continue
			}
			if inj[mode] < want[mode] {
				bad = append(bad, fmt.Sprintf("engine fault %s: armed %d, injected %d", mode, want[mode], inj[mode]))
			}
			if det[mode] < want[mode] {
				bad = append(bad, fmt.Sprintf("engine fault %s: injected %d, detected by the host %d", mode, inj[mode], det[mode]))
			}
			if det[mode+"_undetected"] > 0 {
				bad = append(bad, fmt.Sprintf("engine fault %s passed the reply validator %d times", mode, det[mode+"_undetected"]))
			}
		}
	}
	c.set("engine_faults_effective", bad)

	// db_lock_effective: the injected lock really made the host NACK
	bad = nil
	for _, f := range c.r.Faults {
		if f["fault"] == "db_lock" {
			if c.r.lockNacks.Load() == 0 {
				bad = append(bad, "db_lock held but no update was NACKed while it was held")
			}
			break
		}
	}
	c.set("db_lock_effective", bad)

	// restart_reproduces_state
	bad = nil
	if c.before != nil && c.after != nil {
		bj, _ := json.Marshal(c.before)
		aj, _ := json.Marshal(c.after)
		if string(bj) != string(aj) {
			for k := range c.before {
				x, _ := json.Marshal(c.before[k])
				y, _ := json.Marshal(c.after[k])
				if string(x) != string(y) {
					bad = append(bad, fmt.Sprintf("%s differs after restart: %s -> %s", k, trunc(string(x), 200), trunc(string(y), 200)))
				}
			}
		}
	}
	c.set("restart_reproduces_state", bad)

	// equals_clean_run: same corpus without faults on the correct host
	if c.clean != nil {
		c.set("equals_clean_run", compareRuns(c.scen, st, c.clean, nil))
	}
	// adversarial: malformed updates rejected, other users unaffected, corrupted users repaired
	if len(c.r.RawLines) > 0 || len(c.r.Corrupted) > 0 {
		bad = nil
		statusByLine := map[int]string{}
		for _, a := range c.r.Acks {
			if a.Raw {
				statusByLine[a.Line] = a.Status
			}
		}
		for _, rl := range c.r.RawLines {
			ln := int(rl["line"].(int))
			s := statusByLine[ln]
			if rl["expect"] == "no_effect" {
				if s != "ignored" && s != "rejected" {
					bad = append(bad, fmt.Sprintf("line %d (no_effect) acked %q", ln, s))
				}
			} else if s != "rejected" {
				bad = append(bad, fmt.Sprintf("line %d acked %q, want rejected", ln, s))
			}
		}
		c.set("malformed_updates_rejected", bad)
		if c.pristine != nil {
			skip := map[string]bool{}
			for u := range c.r.Corrupted {
				skip[u] = true
			}
			c.set("other_users_unaffected", compareRuns(c.scen, st, c.pristine, skip))
		}
		bad = nil
		for _, u := range sortedKeys(c.r.Corrupted) {
			mode := c.r.Corrupted[u]
			ok := false
			for _, t := range byUser[u] {
				if (mode == "deleted_node" && t.Recovered) || (mode != "deleted_node" && t.Kind == "repair") {
					ok = true
				}
			}
			if !ok {
				bad = append(bad, fmt.Sprintf("%s (%s) not repaired", u, mode))
			}
		}
		c.set("corrupted_users_repaired", bad)
	}
}

func sumInts(m map[string]int) int {
	s := 0
	for _, v := range m {
		s += v
	}
	return s
}

func asMap(v any) map[string]any {
	m, _ := v.(map[string]any)
	return m
}

// normTrans: the comparable view of a committed transition (update ids of resets are
// runner-local and replaced by "reset").
func normTrans(t Trans) string {
	uid := t.UID
	if strings.HasPrefix(uid, "reset:") {
		uid = "reset"
	}
	a := "-"
	if t.Arg != nil {
		a = *t.Arg
	}
	tok := "-"
	if t.Token != nil {
		tok = fmt.Sprint(*t.Token)
	}
	b, _ := json.Marshal(t.Actions)
	return fmt.Sprintf("%s|%s|%s|%s|%s|%d|%s|%d|%s|%v", uid, t.Event, a, tok, t.Kind, t.Seq, t.At, t.TMs, b, t.Recovered)
}

// compareRuns compares per-user committed transitions and final snapshots. For the
// booking scenario the winner of a race for a scarce slot is timing-dependent
// (documented in shared/README): users are compared through winner-invariant
// aggregates (final at multiset, active reservations per slot, number of transitions).
func compareRuns(scen string, a, b *DBState, skip map[string]bool) []string {
	var bad []string
	ua, ub := changedByUser(a), changedByUser(b)
	if scen == "booking" {
		agg := func(s *DBState, by map[string][]Trans) map[string]any {
			at := map[string]int{}
			for u, x := range s.Users {
				if skip[u] {
					continue
				}
				if sm, ok := x["snapshot"].(map[string]any); ok {
					at[fmt.Sprint(sm["at"])]++
				}
			}
			resv := map[string]int{}
			for _, r := range s.Domain["reservations"] {
				if numOf(r[3]) == 1 {
					resv[fmt.Sprint(r[2])]++
				}
			}
			n := 0
			for u, ts := range by {
				if !skip[u] {
					n += len(ts)
				}
			}
			return map[string]any{"at": at, "reservations": resv, "users": len(s.Users), "transitions": n}
		}
		x, y := agg(a, ua), agg(b, ub)
		xb, _ := json.Marshal(x)
		yb, _ := json.Marshal(y)
		if string(xb) != string(yb) {
			bad = append(bad, fmt.Sprintf("booking aggregates differ: %s vs %s", xb, yb))
		}
		return bad
	}
	all := map[string]bool{}
	for u := range a.Users {
		all[u] = true
	}
	for u := range b.Users {
		all[u] = true
	}
	for _, u := range sortedKeys(all) {
		if skip[u] {
			continue
		}
		if _, ok := a.Users[u]; !ok {
			bad = append(bad, u+": missing in this run")
			continue
		}
		if _, ok := b.Users[u]; !ok {
			bad = append(bad, u+": extra user in this run")
			continue
		}
		sa, _ := json.Marshal(a.Users[u]["snapshot"])
		sb, _ := json.Marshal(b.Users[u]["snapshot"])
		if string(sa) != string(sb) {
			bad = append(bad, fmt.Sprintf("%s: final snapshot %s vs %s", u, sa, sb))
		}
		ta, tb := ua[u], ub[u]
		if len(ta) != len(tb) {
			bad = append(bad, fmt.Sprintf("%s: %d vs %d transitions", u, len(ta), len(tb)))
			continue
		}
		for i := range ta {
			if normTrans(ta[i]) != normTrans(tb[i]) {
				bad = append(bad, fmt.Sprintf("%s #%d: %s vs %s", u, i, normTrans(ta[i]), normTrans(tb[i])))
				break
			}
		}
	}
	return bad
}

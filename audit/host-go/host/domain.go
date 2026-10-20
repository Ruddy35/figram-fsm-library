package host

// Domain of the four shared test bots (normative text: hosts/shared/domain.md).
// Guards are computed from the host DB at step time; calls mutate it (local) or go
// to the simulated CRM (external) with the outbox key as idempotency key.

import (
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"unicode"
)

const domainSchema = `
CREATE TABLE IF NOT EXISTS zones(street TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS prefs(user TEXT PRIMARY KEY, size TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS cart(key TEXT PRIMARY KEY, user TEXT NOT NULL, size TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS cart_user ON cart(user);
CREATE TABLE IF NOT EXISTS addresses(user TEXT PRIMARY KEY, address TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS orders_local(key TEXT PRIMARY KEY, user TEXT NOT NULL, order_id INTEGER NOT NULL,
  items TEXT NOT NULL, address TEXT);
CREATE TABLE IF NOT EXISTS slots(slot TEXT PRIMARY KEY, capacity INTEGER NOT NULL CHECK(capacity >= 0));
CREATE TABLE IF NOT EXISTS selection(user TEXT PRIMARY KEY, slot TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS reservations(key TEXT PRIMARY KEY, user TEXT NOT NULL, slot TEXT NOT NULL,
  active INTEGER NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS reservations_user_slot ON reservations(user, slot) WHERE active = 1;
CREATE TABLE IF NOT EXISTS calendar_local(key TEXT PRIMARY KEY, user TEXT NOT NULL, event_id INTEGER);
CREATE TABLE IF NOT EXISTS tickets(user TEXT PRIMARY KEY, ticket_id INTEGER NOT NULL, state TEXT NOT NULL,
  messages INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS reminders(user TEXT PRIMARY KEY, text TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS pings(key TEXT PRIMARY KEY, user TEXT NOT NULL, text TEXT);
CREATE TABLE IF NOT EXISTS notifications_local(key TEXT PRIMARY KEY, user TEXT NOT NULL);
`

var (
	Zones = []string{"Lenina", "Mira", "Pushkina", "Gagarina"}
	Slots = map[string]int{"s1": 1, "s2": 2, "s3": 4, "s4": 10}
)

func seedDomain(tx *sql.Tx) error {
	for _, z := range Zones {
		if _, err := tx.Exec(`INSERT OR IGNORE INTO zones(street) VALUES(?)`, z); err != nil {
			return err
		}
	}
	for s, c := range Slots {
		if _, err := tx.Exec(`INSERT OR IGNORE INTO slots(slot, capacity) VALUES(?,?)`, s, c); err != nil {
			return err
		}
	}
	return nil
}

// Querier is satisfied by *sql.DB, *sql.Tx and *sql.Conn.
type Querier interface {
	QueryRow(string, ...any) *sql.Row
	Query(string, ...any) (*sql.Rows, error)
	Exec(string, ...any) (sql.Result, error)
}

// StreetOf: first token of the update's arg (split on whitespace/comma).
func StreetOf(arg string) string {
	f := strings.FieldsFunc(arg, func(r rune) bool { return unicode.IsSpace(r) || r == ',' })
	if len(f) == 0 {
		return ""
	}
	return f[0]
}

func count(q Querier, sqlText string, args ...any) (int, error) {
	var n int
	err := q.QueryRow(sqlText, args...).Scan(&n)
	return n, err
}

func selectedSlot(q Querier, user string) (string, bool, error) {
	var s string
	err := q.QueryRow(`SELECT slot FROM selection WHERE user=?`, user).Scan(&s)
	if errors.Is(err, sql.ErrNoRows) {
		return "", false, nil
	}
	return s, err == nil, err
}

func reminderText(q Querier, user string) (string, bool, error) {
	var t string
	err := q.QueryRow(`SELECT text FROM reminders WHERE user=?`, user).Scan(&t)
	if errors.Is(err, sql.ErrNoRows) {
		return "", false, nil
	}
	return t, err == nil, err
}

func ticketOf(q Querier, user string) (id int64, state string, ok bool, err error) {
	err = q.QueryRow(`SELECT ticket_id, state FROM tickets WHERE user=?`, user).Scan(&id, &state)
	if errors.Is(err, sql.ErrNoRows) {
		return 0, "", false, nil
	}
	return id, state, err == nil, err
}

// GuardCtx is what a guard may look at besides the DB.
type GuardCtx struct {
	User         string
	Arg          string
	CountIntents bool // rule G2: count other users' pending reserve_slot rows (broken: no-resource-lock)
}

// EvalGuard computes one guard from the domain DB.
func EvalGuard(q Querier, name string, g GuardCtx) (bool, error) {
	u := g.User
	switch name {
	case "cart_not_empty":
		n, err := count(q, `SELECT COUNT(*) FROM cart WHERE user=?`, u)
		return n > 0, err
	case "in_delivery_zone":
		st := StreetOf(g.Arg)
		if st == "" {
			return false, nil
		}
		n, err := count(q, `SELECT COUNT(*) FROM zones WHERE street=?`, st)
		return n > 0, err
	case "slot_free":
		s, ok, err := selectedSlot(q, u)
		if err != nil || !ok {
			return false, err
		}
		var capacity int
		if err := q.QueryRow(`SELECT capacity FROM slots WHERE slot=?`, s).Scan(&capacity); err != nil {
			if errors.Is(err, sql.ErrNoRows) {
				return false, nil
			}
			return false, err
		}
		active, err := count(q, `SELECT COUNT(*) FROM reservations WHERE slot=? AND active=1`, s)
		if err != nil {
			return false, err
		}
		mine, err := count(q, `SELECT COUNT(*) FROM reservations WHERE slot=? AND active=1 AND user=?`, s, u)
		if err != nil {
			return false, err
		}
		pending := 0
		if g.CountIntents {
			pending, err = count(q, `SELECT COUNT(*) FROM outbox WHERE state='pending' AND kind='call'
				AND name='reserve_slot' AND param=? AND user<>?`, s, u)
			if err != nil {
				return false, err
			}
		}
		return mine == 0 && active+pending < capacity, nil
	case "has_booking":
		n, err := count(q, `SELECT COUNT(*) FROM reservations WHERE user=? AND active=1`, u)
		return n > 0, err
	case "can_escalate":
		_, st, ok, err := ticketOf(q, u)
		return ok && st == "open", err
	case "ticket_open":
		_, st, ok, err := ticketOf(q, u)
		return ok && (st == "open" || st == "escalated"), err
	case "has_reminder":
		t, ok, err := reminderText(q, u)
		return ok && t != "", err
	case "nag_enabled":
		t, ok, err := reminderText(q, u)
		return ok && strings.HasPrefix(t, "!"), err
	}
	return false, fmt.Errorf("no guard function for %q", name)
}

// GuardResource names the shared resource lock a guard needs (rule G2), or "".
func GuardResource(q Querier, name, user string) (string, error) {
	if name != "slot_free" {
		return "", nil
	}
	s, ok, err := selectedSlot(q, user)
	if err != nil || !ok {
		return "", err
	}
	return "slot:" + s, nil
}

// CallSpec describes a call of the shared bots.
type CallSpec struct {
	Name     string
	External bool
	Op       string // CRM operation for external calls
	Param    string // "", "arg", "selection"
}

var Calls = map[string]CallSpec{}

func init() {
	for _, c := range []CallSpec{
		{Name: "clear_cart"}, {Name: "save_size", Param: "arg"}, {Name: "add_to_cart"},
		{Name: "save_address", Param: "arg"}, {Name: "create_order", External: true, Op: "create_order"},
		{Name: "choose_slot", Param: "arg"}, {Name: "reserve_slot", Param: "selection"},
		{Name: "notify_calendar", External: true, Op: "calendar", Param: "selection"},
		{Name: "release_hold"}, {Name: "cancel_booking"},
		{Name: "create_ticket", External: true, Op: "ticket_create", Param: "arg"},
		{Name: "log_message", External: true, Op: "ticket_message", Param: "arg"},
		{Name: "escalate", External: true, Op: "ticket_escalate"},
		{Name: "close_ticket", External: true, Op: "ticket_close"},
		{Name: "save_reminder", Param: "arg"}, {Name: "clear_reminder"}, {Name: "log_ping"},
		{Name: "notify_owner", External: true, Op: "notify"},
	} {
		Calls[c.Name] = c
	}
}

// ResolveParam fixes a call parameter inside the step transaction.
func ResolveParam(q Querier, c CallSpec, user string, arg *string) (sql.NullString, error) {
	switch c.Param {
	case "arg":
		if arg == nil {
			return sql.NullString{String: "", Valid: true}, nil
		}
		return sql.NullString{String: *arg, Valid: true}, nil
	case "selection":
		s, ok, err := selectedSlot(q, user)
		if err != nil {
			return sql.NullString{}, err
		}
		return sql.NullString{String: s, Valid: ok}, nil
	}
	return sql.NullString{}, nil
}

// ErrPermanent marks a non-retryable call failure (outbox row -> failed).
type ErrPermanent struct{ Msg string }

func (e *ErrPermanent) Error() string { return "permanent: " + e.Msg }

// ApplyLocal applies a local call inside the executor transaction that marks the row done.
func ApplyLocal(tx *sql.Tx, name, user, key string, param sql.NullString, checkCapacity bool) error {
	p := param.String
	var err error
	switch name {
	case "clear_cart":
		_, err = tx.Exec(`DELETE FROM cart WHERE user=?`, user)
	case "save_size":
		if p == "" {
			p = "M"
		}
		_, err = tx.Exec(`INSERT INTO prefs(user,size) VALUES(?,?) ON CONFLICT(user) DO UPDATE SET size=excluded.size`, user, p)
	case "add_to_cart":
		size := "M"
		var s string
		if e := tx.QueryRow(`SELECT size FROM prefs WHERE user=?`, user).Scan(&s); e == nil {
			size = s
		} else if !errors.Is(e, sql.ErrNoRows) {
			return e
		}
		_, err = tx.Exec(`INSERT INTO cart(key,user,size) VALUES(?,?,?) ON CONFLICT(key) DO NOTHING`, key, user, size)
	case "save_address":
		_, err = tx.Exec(`INSERT INTO addresses(user,address) VALUES(?,?) ON CONFLICT(user) DO UPDATE SET address=excluded.address`, user, p)
	case "choose_slot":
		_, err = tx.Exec(`INSERT INTO selection(user,slot) VALUES(?,?) ON CONFLICT(user) DO UPDATE SET slot=excluded.slot`, user, p)
	case "reserve_slot":
		if !param.Valid {
			return &ErrPermanent{"reserve_slot without a selected slot"}
		}
		var capacity int
		known := true
		e := tx.QueryRow(`SELECT capacity FROM slots WHERE slot=?`, p).Scan(&capacity)
		if errors.Is(e, sql.ErrNoRows) {
			known = false
		} else if e != nil {
			return e
		}
		active, e := count(tx, `SELECT COUNT(*) FROM reservations WHERE slot=? AND active=1`, p)
		if e != nil {
			return e
		}
		if checkCapacity && (!known || active >= capacity) {
			return &ErrPermanent{fmt.Sprintf("slot %s is full (%d/%d)", p, active, capacity)}
		}
		if _, e := tx.Exec(`INSERT INTO reservations(key,user,slot,active) VALUES(?,?,?,1) ON CONFLICT(key) DO NOTHING`, key, user, p); e != nil {
			if IsConstraint(e) {
				return &ErrPermanent{"duplicate reservation: " + e.Error()}
			}
			return e
		}
	case "release_hold":
		_, err = tx.Exec(`DELETE FROM selection WHERE user=?`, user)
	case "cancel_booking":
		_, err = tx.Exec(`UPDATE reservations SET active=0 WHERE user=? AND active=1`, user)
	case "save_reminder":
		if p != "" {
			_, err = tx.Exec(`INSERT INTO reminders(user,text) VALUES(?,?) ON CONFLICT(user) DO UPDATE SET text=excluded.text`, user, p)
		} else {
			_, err = tx.Exec(`DELETE FROM reminders WHERE user=?`, user)
		}
	case "clear_reminder":
		_, err = tx.Exec(`DELETE FROM reminders WHERE user=?`, user)
	case "log_ping":
		t, ok, e := reminderText(tx, user)
		if e != nil {
			return e
		}
		var txt any
		if ok {
			txt = t
		}
		_, err = tx.Exec(`INSERT INTO pings(key,user,text) VALUES(?,?,?) ON CONFLICT(key) DO NOTHING`, key, user, txt)
	default:
		return &ErrPermanent{"unknown local call " + name}
	}
	return err
}

// ExternalPayload is read from the domain DB at execution time (the user's earlier
// rows are all executed, so it is deterministic).
func ExternalPayload(q Querier, name, user string, param sql.NullString) (map[string]any, error) {
	switch name {
	case "create_order":
		rows, err := q.Query(`SELECT size FROM cart WHERE user=? ORDER BY key`, user)
		if err != nil {
			return nil, err
		}
		items := []string{}
		for rows.Next() {
			var s string
			if err := rows.Scan(&s); err != nil {
				rows.Close()
				return nil, err
			}
			items = append(items, s)
		}
		rows.Close()
		var addr any
		var a string
		if err := q.QueryRow(`SELECT address FROM addresses WHERE user=?`, user).Scan(&a); err == nil {
			addr = a
		} else if !errors.Is(err, sql.ErrNoRows) {
			return nil, err
		}
		return map[string]any{"items": items, "address": addr}, nil
	case "notify_calendar":
		var s any
		if param.Valid {
			s = param.String
		}
		return map[string]any{"slot": s}, nil
	case "create_ticket", "log_message":
		var tid any
		if name == "log_message" {
			id, _, ok, err := ticketOf(q, user)
			if err != nil {
				return nil, err
			}
			if ok {
				tid = id
			}
		}
		return map[string]any{"text": param.String, "ticket_id": tid}, nil
	case "escalate", "close_ticket":
		id, _, ok, err := ticketOf(q, user)
		if err != nil {
			return nil, err
		}
		var tid any
		if ok {
			tid = id
		}
		return map[string]any{"ticket_id": tid}, nil
	case "notify_owner":
		t, _, err := reminderText(q, user)
		if err != nil {
			return nil, err
		}
		return map[string]any{"text": t}, nil
	}
	return nil, &ErrPermanent{"unknown external call " + name}
}

// ApplyExternalResult mirrors a CRM result into the domain DB (same txn as "done").
func ApplyExternalResult(tx *sql.Tx, name, user, key string, payload map[string]any, result map[string]any) error {
	num := func(k string) int64 {
		switch v := result[k].(type) {
		case float64:
			return int64(v)
		case json.Number:
			n, _ := v.Int64()
			return n
		}
		return 0
	}
	var err error
	switch name {
	case "create_order":
		items, _ := json.Marshal(payload["items"])
		if _, err = tx.Exec(`INSERT INTO orders_local(key,user,order_id,items,address) VALUES(?,?,?,?,?) ON CONFLICT(key) DO NOTHING`,
			key, user, num("order_id"), string(items), payload["address"]); err != nil {
			return err
		}
		_, err = tx.Exec(`DELETE FROM cart WHERE user=?`, user)
	case "notify_calendar":
		_, err = tx.Exec(`INSERT INTO calendar_local(key,user,event_id) VALUES(?,?,?) ON CONFLICT(key) DO NOTHING`, key, user, num("event_id"))
	case "create_ticket":
		_, err = tx.Exec(`INSERT INTO tickets(user,ticket_id,state,messages) VALUES(?,?,'open',1)
			ON CONFLICT(user) DO UPDATE SET ticket_id=excluded.ticket_id, state='open', messages=1`, user, num("ticket_id"))
	case "log_message":
		_, err = tx.Exec(`UPDATE tickets SET messages=messages+1 WHERE user=?`, user)
	case "escalate":
		_, err = tx.Exec(`UPDATE tickets SET state='escalated' WHERE user=?`, user)
	case "close_ticket":
		_, err = tx.Exec(`UPDATE tickets SET state='closed' WHERE user=?`, user)
	case "notify_owner":
		_, err = tx.Exec(`INSERT INTO notifications_local(key,user) VALUES(?,?) ON CONFLICT(key) DO NOTHING`, key, user)
	}
	return err
}

// ResetUserData: "delete my data, keep the account" (domain.md §5).
func ResetUserData(tx *sql.Tx, family, user string) error {
	var tables []string
	switch family {
	case "pizza":
		tables = []string{"cart", "prefs", "addresses"}
	case "booking":
		tables = []string{"selection"}
	case "reminders":
		tables = []string{"reminders"}
	}
	for _, t := range tables {
		if _, err := tx.Exec(`DELETE FROM `+t+` WHERE user=?`, user); err != nil {
			return err
		}
	}
	return nil
}

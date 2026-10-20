package host

import (
	"bytes"
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math/rand"
	"net"
	"net/http"
	"strconv"
	"sync"
	"sync/atomic"
	"time"
)

// ---------------------------------------------------------------- external client

// ExtTransient is a retryable external failure (500/429/timeout/connection).
type ExtTransient struct {
	Code         string
	RetryAfterMs int64
}

func (e *ExtTransient) Error() string { return "external transient: " + e.Code }

// ExtClient talks to the simulated CRM / Telegram over a unix socket (HTTP).
type ExtClient struct{ c *http.Client }

func NewExtClient(sock string, timeout time.Duration) *ExtClient {
	tr := &http.Transport{
		DialContext: func(ctx context.Context, _, _ string) (net.Conn, error) {
			var d net.Dialer
			return d.DialContext(ctx, "unix", sock)
		},
		MaxIdleConnsPerHost: 16,
	}
	return &ExtClient{c: &http.Client{Transport: tr, Timeout: timeout}}
}

func (x *ExtClient) post(path string, hdr map[string]string, body any) (map[string]any, error) {
	b, _ := json.Marshal(body)
	req, _ := http.NewRequest("POST", "http://ext"+path, bytes.NewReader(b))
	req.Header.Set("Content-Type", "application/json")
	for k, v := range hdr {
		req.Header.Set(k, v)
	}
	resp, err := x.c.Do(req)
	if err != nil {
		return nil, &ExtTransient{Code: "timeout"}
	}
	defer resp.Body.Close()
	rb, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, &ExtTransient{Code: "timeout"}
	}
	switch {
	case resp.StatusCode == 200:
		var out map[string]any
		if err := json.Unmarshal(rb, &out); err != nil {
			return nil, &ExtTransient{Code: "bad_body"}
		}
		return out, nil
	case resp.StatusCode == 429:
		ms, _ := strconv.ParseInt(resp.Header.Get("Retry-After-Ms"), 10, 64)
		return nil, &ExtTransient{Code: "429", RetryAfterMs: ms}
	case resp.StatusCode == 422:
		return nil, &ErrPermanent{string(rb)}
	default:
		return nil, &ExtTransient{Code: strconv.Itoa(resp.StatusCode)}
	}
}

// CRM performs an external call with an idempotency key (Stripe-like).
func (x *ExtClient) CRM(op, key, user string, payload map[string]any) (map[string]any, error) {
	return x.post("/crm/"+op, map[string]string{"Idempotency-Key": key}, map[string]any{"user": user, "payload": payload})
}

// Send delivers a Telegram message. Telegram has NO idempotency key: the outbox key
// travels only as a debug header so the simulator can count duplicates.
func (x *ExtClient) Send(key, user, text string) (map[string]any, error) {
	return x.post("/tg/send", map[string]string{"X-Outbox-Key": key}, map[string]any{"user": user, "text": text})
}

// ---------------------------------------------------------------- outbox execution

type outRow struct {
	id       int64
	key      string
	kind     string
	name     string
	param    sql.NullString
	attempts int64
	user     string
}

// drainUser: rule G1 — execute the user's pending outbox prefix up to and including
// the last pending call, synchronously, before guards are computed.
func (h *Host) drainUser(user string) error {
	var maxID sql.NullInt64
	if err := h.db.QueryRow(`SELECT MAX(id) FROM outbox WHERE user=? AND state='pending' AND kind='call'`, user).Scan(&maxID); err != nil {
		return err
	}
	if !maxID.Valid {
		return nil
	}
	h.stat("g1_drain", 1)
	ol := h.lockOf(h.olocks, user)
	ol.Lock()
	defer ol.Unlock()
	for attempt := 0; attempt < 6; attempt++ {
		done, backoff, err := h.runOutbox(user, maxID.Int64)
		if err != nil {
			return err
		}
		if done {
			return nil
		}
		sleepBackoff(backoff)
	}
	return &ErrNack{errDrainFailed.Error()}
}

// runOutbox executes pending call/send rows of a user in id order (caller holds the
// user's outbox lock). done=false means a transient failure (retry after backoff).
func (h *Host) runOutbox(user string, upto int64) (bool, int64, error) {
	q := `SELECT id,key,kind,name,param,attempts FROM outbox WHERE user=? AND state='pending' AND kind IN ('call','send')`
	args := []any{user}
	if upto > 0 {
		q += ` AND id<=?`
		args = append(args, upto)
	}
	q += ` ORDER BY id`
	rows, err := h.db.Query(q, args...)
	if err != nil {
		return false, 50, nil
	}
	var list []outRow
	for rows.Next() {
		var r outRow
		if err := rows.Scan(&r.id, &r.key, &r.kind, &r.name, &r.param, &r.attempts); err != nil {
			rows.Close()
			return false, 50, nil
		}
		r.user = user
		list = append(list, r)
	}
	rows.Close()
	for _, r := range list {
		ok, backoff := h.execRow(r)
		if !ok {
			return false, backoff, nil
		}
	}
	return true, 0, nil
}

func (h *Host) transient(r outRow, code string, retryMs int64) (bool, int64) {
	h.stat("ext_"+code, 1)
	h.db.Exec(`UPDATE outbox SET attempts=attempts+1, last_error=? WHERE id=?`, code, r.id)
	b := int64(5) << uint(min64(r.attempts, 6))
	if b > 200 {
		b = 200
	}
	if retryMs > b {
		b = retryMs
	}
	return false, b
}

func min64(a, b int64) int64 {
	if a < b {
		return a
	}
	return b
}

func (h *Host) fail(r outRow, msg string) (bool, int64) {
	h.db.Exec(`UPDATE outbox SET state='failed', last_error=? WHERE id=?`, msg, r.id)
	h.stat("outbox_failed", 1)
	h.log("outbox_failed", r.user, map[string]any{"key": r.key, "error": msg})
	return true, 0
}

// execRow executes one outbox row. Returns ok=true when the row reached a final
// state (done/failed) or was already final.
func (h *Host) execRow(r outRow) (bool, int64) {
	tag := "send"
	if r.kind == "call" {
		tag = "call:" + r.name
	}
	h.crashPoint("before_effect", tag)
	if r.kind == "call" {
		spec, ok := Calls[r.name]
		if !ok {
			return h.fail(r, "unknown call "+r.name)
		}
		if !spec.External {
			return h.execLocal(r, tag)
		}
		return h.execExternal(r, spec, tag)
	}
	res, err := h.ext.Send(r.key, r.user, r.name)
	if err != nil {
		var t *ExtTransient
		if errors.As(err, &t) {
			return h.transient(r, "tg_"+t.Code, t.RetryAfterMs)
		}
		return h.fail(r, err.Error())
	}
	h.crashPoint("after_effect_before_ack", tag)
	rb, _ := json.Marshal(res)
	if err := h.persistAck(func(tx *sql.Tx) error {
		_, err := tx.Exec(`UPDATE outbox SET state='done', done_ms=?, attempts=attempts+1, result=? WHERE id=? AND state='pending'`,
			h.now(), string(rb), r.id)
		return err
	}); err != nil {
		h.stat("exec_db_error", 1)
		return false, 100
	}
	h.stat("exec_send", 1)
	return true, 0
}

func (h *Host) execLocal(r outRow, tag string) (bool, int64) {
	tx, err := h.db.Begin()
	if err != nil {
		h.stat("exec_db_error", 1)
		return false, 50
	}
	defer tx.Rollback()
	var st string
	if err := tx.QueryRow(`SELECT state FROM outbox WHERE id=?`, r.id).Scan(&st); err != nil {
		return false, 50
	}
	if st != "pending" {
		return true, 0
	}
	if err := ApplyLocal(tx, r.name, r.user, r.key, r.param, !h.broken["no-capacity-check"]); err != nil {
		var p *ErrPermanent
		if errors.As(err, &p) {
			tx.Exec(`UPDATE outbox SET state='failed', last_error=? WHERE id=?`, p.Error(), r.id)
			if tx.Commit() != nil {
				return false, 50
			}
			h.stat("outbox_failed", 1)
			h.log("outbox_failed", r.user, map[string]any{"key": r.key, "error": p.Error()})
			return true, 0
		}
		h.stat("exec_db_error", 1)
		return false, 50
	}
	h.crashPoint("after_effect_before_ack", tag) // inside the txn: the local effect dies with it
	if _, err := tx.Exec(`UPDATE outbox SET state='done', done_ms=?, attempts=attempts+1 WHERE id=?`, h.now(), r.id); err != nil {
		return false, 50
	}
	if err := tx.Commit(); err != nil {
		h.stat("exec_db_error", 1)
		return false, 50
	}
	h.stat("exec_local", 1)
	return true, 0
}

var nonceRng = struct {
	sync.Mutex
	*rand.Rand
}{Rand: rand.New(rand.NewSource(time.Now().UnixNano()))}

func (h *Host) execExternal(r outRow, spec CallSpec, tag string) (bool, int64) {
	if h.broken["skip-external"] {
		h.db.Exec(`UPDATE outbox SET state='done', done_ms=? WHERE id=?`, h.now(), r.id) // BROKEN
		return true, 0
	}
	payload, err := ExternalPayload(h.db, r.name, r.user, r.param)
	if err != nil {
		var p *ErrPermanent
		if errors.As(err, &p) {
			return h.fail(r, err.Error())
		}
		return false, 50
	}
	key := r.key
	if h.broken["random-keys"] {
		nonceRng.Lock()
		key = fmt.Sprintf("%s#%08x", r.key, nonceRng.Uint32()) // BROKEN: new key per attempt
		nonceRng.Unlock()
	}
	res, err := h.ext.CRM(spec.Op, key, r.user, payload)
	if err != nil {
		var t *ExtTransient
		if errors.As(err, &t) {
			return h.transient(r, t.Code, t.RetryAfterMs)
		}
		return h.fail(r, err.Error())
	}
	result, _ := res["result"].(map[string]any)
	h.crashPoint("after_effect_before_ack", tag)
	rb, _ := json.Marshal(result)
	if err := h.persistAck(func(tx *sql.Tx) error {
		var st string
		if err := tx.QueryRow(`SELECT state FROM outbox WHERE id=?`, r.id).Scan(&st); err != nil {
			return err
		}
		if st != "pending" {
			return nil
		}
		if err := ApplyExternalResult(tx, r.name, r.user, r.key, payload, result); err != nil {
			return err
		}
		_, err := tx.Exec(`UPDATE outbox SET state='done', done_ms=?, attempts=attempts+1, result=? WHERE id=?`,
			h.now(), string(rb), r.id)
		return err
	}); err != nil {
		h.stat("exec_db_error", 1)
		return false, 100
	}
	h.stat("exec_external", 1)
	return true, 0
}

// persistAck: the external effect already happened — keep retrying the local "done"
// write while the DB is busy instead of re-executing the effect (a Telegram duplicate).
func (h *Host) persistAck(fn func(tx *sql.Tx) error) error {
	budget := 30 * time.Second
	if h.broken["no-ack-retry"] {
		budget = 0
	}
	end := time.Now().Add(budget)
	delay := 10 * time.Millisecond
	for {
		err := func() error {
			tx, err := h.db.Begin()
			if err != nil {
				return err
			}
			defer tx.Rollback()
			if err := fn(tx); err != nil {
				return err
			}
			return tx.Commit()
		}()
		if err == nil {
			return nil
		}
		h.stat("ack_write_retry", 1)
		if time.Now().After(end) {
			return err
		}
		time.Sleep(delay)
		if delay < 200*time.Millisecond {
			delay *= 2
		}
	}
}

// ---------------------------------------------------------------- executor

// Executor: N workers; rows of one user strictly in order (per-user outbox lock).
type Executor struct {
	h       *Host
	q       chan string
	mu      sync.Mutex
	queued  map[string]bool
	paused  atomic.Bool
	busy    atomic.Int64
	stopped atomic.Bool
}

func newExecutor(h *Host, n int) *Executor {
	e := &Executor{h: h, q: make(chan string, 100000), queued: map[string]bool{}}
	for i := 0; i < n; i++ {
		go e.loop()
	}
	go e.sweep()
	return e
}

func (e *Executor) notify(user string) {
	e.mu.Lock()
	if e.queued[user] {
		e.mu.Unlock()
		return
	}
	e.queued[user] = true
	e.mu.Unlock()
	select {
	case e.q <- user:
	default:
		e.mu.Lock()
		delete(e.queued, user)
		e.mu.Unlock()
	}
}

func (e *Executor) kickAll() {
	rows, err := e.h.db.Query(`SELECT DISTINCT user FROM outbox WHERE state='pending' AND kind IN ('call','send')`)
	if err != nil {
		return
	}
	var us []string
	for rows.Next() {
		var u string
		if rows.Scan(&u) == nil {
			us = append(us, u)
		}
	}
	rows.Close()
	for _, u := range us {
		e.notify(u)
	}
}

func (e *Executor) sweep() {
	for !e.stopped.Load() {
		time.Sleep(200 * time.Millisecond)
		if !e.paused.Load() {
			e.kickAll()
		}
	}
}

func (e *Executor) loop() {
	for user := range e.q {
		e.mu.Lock()
		delete(e.queued, user)
		e.mu.Unlock()
		if e.stopped.Load() {
			return
		}
		e.busy.Add(1)
		for e.paused.Load() && !e.stopped.Load() {
			time.Sleep(10 * time.Millisecond)
		}
		ol := e.h.lockOf(e.h.olocks, user)
		ol.Lock()
		done, backoff, _ := e.h.runOutbox(user, 0)
		ol.Unlock()
		if !done {
			u := user
			time.AfterFunc(time.Duration(backoff)*time.Millisecond, func() { e.notify(u) })
		}
		e.busy.Add(-1)
	}
}

func (e *Executor) stop() { e.stopped.Store(true) }

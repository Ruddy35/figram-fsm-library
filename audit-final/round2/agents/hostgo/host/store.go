package main

// Storage layer: SQLite (modernc.org/sqlite, pure Go, from the offline module cache) in WAL mode.
// One writer connection (all transactions are serialized through it, as SQLite allows one
// writer anyway) and a small pool of reader connections. A "crash" of the host instance abandons
// every transaction that has not committed (Rollback) and every in-memory structure; the next
// instance recovers only from what is committed here.

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"os"
	"sync"
	"time"

	_ "modernc.org/sqlite"
)

const schema = `
CREATE TABLE IF NOT EXISTS docs(name TEXT PRIMARY KEY, ver TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS users(user TEXT PRIMARY KEY, bot TEXT NOT NULL, snap TEXT, ver INTEGER NOT NULL, hwm INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS processed(user TEXT NOT NULL, uid TEXT NOT NULL, PRIMARY KEY(user, uid)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS inbound(id INTEGER PRIMARY KEY AUTOINCREMENT, user TEXT NOT NULL, uid TEXT NOT NULL, kind TEXT NOT NULL,
  event TEXT NOT NULL, token INTEGER, run TEXT, data TEXT, t INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS inbound_user ON inbound(user, id);
CREATE TABLE IF NOT EXISTS outbox(id INTEGER PRIMARY KEY AUTOINCREMENT, user TEXT NOT NULL, seq INTEGER NOT NULL, pos INTEGER NOT NULL,
  key TEXT NOT NULL UNIQUE, kind TEXT NOT NULL, name TEXT NOT NULL, token INTEGER, detached INTEGER NOT NULL, run TEXT,
  ms INTEGER, due INTEGER, data TEXT, state INTEGER NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, docv TEXT,
  t_commit INTEGER NOT NULL, t_done INTEGER);
CREATE INDEX IF NOT EXISTS outbox_state ON outbox(state, detached, user, seq, pos);
CREATE INDEX IF NOT EXISTS outbox_call ON outbox(user, name, token);
CREATE INDEX IF NOT EXISTS outbox_user ON outbox(user, detached, state, seq, pos);
CREATE TABLE IF NOT EXISTS timers(user TEXT NOT NULL, name TEXT NOT NULL, token INTEGER NOT NULL, run TEXT NOT NULL, due INTEGER NOT NULL,
  PRIMARY KEY(user, name)) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS timers_due ON timers(due);
CREATE TABLE IF NOT EXISTS effects(key TEXT PRIMARY KEY, user TEXT NOT NULL, action TEXT NOT NULL) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS kv(user TEXT NOT NULL, k TEXT NOT NULL, v INTEGER NOT NULL, PRIMARY KEY(user, k)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS steplog(id INTEGER PRIMARY KEY AUTOINCREMENT, user TEXT NOT NULL, op TEXT NOT NULL, uid TEXT NOT NULL,
  event TEXT, token INTEGER, data TEXT, guards TEXT, docv TEXT, hwm INTEGER, pre TEXT, post TEXT, actions TEXT, recovered INTEGER,
  t_in INTEGER, t_commit INTEGER, wait_us INTEGER);
CREATE INDEX IF NOT EXISTS steplog_user ON steplog(user, id);
CREATE TABLE IF NOT EXISTS drops(user TEXT, uid TEXT, reason TEXT);
CREATE TABLE IF NOT EXISTS failq(outbox_id INTEGER, user TEXT, name TEXT, detached INTEGER, reason TEXT);
CREATE TABLE IF NOT EXISTS outcome_log(user TEXT, action TEXT, token INTEGER, run TEXT, outcome TEXT, via TEXT);
CREATE TABLE IF NOT EXISTS ingest_log(user TEXT, uid TEXT, event TEXT);
`

type txLike = *sql.Tx

type Store struct {
	wmu sync.Mutex
	w *sql.DB // single writer connection
	r *sql.DB // readers
}

var errCrashed = errors.New("host instance crashed")

func openStore(path string, syncMode string, fresh bool) (*Store, error) {
	if fresh {
		for _, suf := range []string{"", "-wal", "-shm"} {
			os.Remove(path + suf)
		}
	}
	dsn := fmt.Sprintf("file:%s?_pragma=journal_mode(WAL)&_pragma=synchronous(%s)&_pragma=busy_timeout(20000)&_pragma=foreign_keys(0)&_txlock=immediate", path, syncMode)
	w, err := sql.Open("sqlite", dsn)
	if err != nil {
		return nil, err
	}
	w.SetMaxOpenConns(1)
	w.SetMaxIdleConns(1)
	if _, err := w.Exec(schema); err != nil {
		return nil, err
	}
	r, err := sql.Open("sqlite", fmt.Sprintf("file:%s?_pragma=busy_timeout(20000)&mode=ro", path))
	if err != nil {
		return nil, err
	}
	r.SetMaxOpenConns(4)
	r.SetMaxIdleConns(4)
	if os.Getenv("HOSTGO_READ_ON_WRITER") == "1" {
		r.Close()
		r = w
	}
	return &Store{w: w, r: r}, nil
}

func (s *Store) Close() { s.w.Close(); s.r.Close() }

// tx runs fn in a write transaction of instance in. The instance is checked right before the
// commit: a crashed instance never commits (in a real crash the commit either happened before
// the crash or not at all; both orders are covered by the crash points).
func (s *Store) tx(in *Instance, fn func(txLike) error) error {
	if in != nil && in.dead() {
		return errCrashed
	}
	tw := time.Now()
	s.wmu.Lock()
	defer s.wmu.Unlock()
	t0 := time.Now()
	t, err := s.w.BeginTx(context.Background(), nil)
	if err != nil {
		return err
	}
	t1 := time.Now()
	txProf.addWait(t0.Sub(tw))
	if err := fn(t); err != nil {
		t.Rollback()
		return err
	}
	if in != nil && in.dead() {
		t.Rollback()
		return errCrashed
	}
	t2 := time.Now()
	err = t.Commit()
	t3 := time.Now()
	txProf.add(t1.Sub(t0), t2.Sub(t1), t3.Sub(t2))
	return err
}

type txProfile struct {
	mu                  sync.Mutex
	n                   int64
	begin, body, commit time.Duration
	maxBegin            time.Duration
	wait                time.Duration
}

func (p *txProfile) addWait(d time.Duration) { p.mu.Lock(); p.wait += d; p.mu.Unlock() }

var txProf txProfile

func (p *txProfile) add(b, body, c time.Duration) {
	p.mu.Lock()
	p.n++
	p.begin += b
	p.body += body
	p.commit += c
	if b > p.maxBegin {
		p.maxBegin = b
	}
	p.mu.Unlock()
}

func (p *txProfile) String() string {
	p.mu.Lock()
	defer p.mu.Unlock()
	if p.n == 0 {
		return "no tx"
	}
	return fmt.Sprintf("tx %d: mean wait %v, begin %v (max %v), body %v, commit %v", p.n, p.wait/time.Duration(p.n), p.begin/time.Duration(p.n), p.maxBegin, p.body/time.Duration(p.n), p.commit/time.Duration(p.n))
}

package host

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"strings"
	"time"

	"modernc.org/sqlite"
	sqlite3 "modernc.org/sqlite/lib"
)

// Schema of the host database. One file, WAL, synchronous=FULL by default.
const schema = `
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS users(
  user TEXT PRIMARY KEY, family TEXT NOT NULL, doc TEXT NOT NULL, snapshot TEXT,
  version INTEGER NOT NULL, hw_seq INTEGER NOT NULL CHECK(hw_seq >= 0),
  created_ms INTEGER, updated_ms INTEGER);
CREATE TABLE IF NOT EXISTS processed(
  user TEXT NOT NULL, update_id TEXT NOT NULL, status TEXT NOT NULL, t_ms INTEGER,
  PRIMARY KEY(user, update_id));
CREATE TABLE IF NOT EXISTS transitions(
  n INTEGER PRIMARY KEY AUTOINCREMENT, user TEXT NOT NULL, update_id TEXT, event TEXT, arg TEXT,
  token INTEGER, kind TEXT NOT NULL, seq_before INTEGER, seq INTEGER, at TEXT, doc TEXT,
  snapshot TEXT, actions TEXT, recovered INTEGER NOT NULL DEFAULT 0, t_ms INTEGER, guards TEXT,
  changed INTEGER NOT NULL DEFAULT 1, note TEXT);
CREATE INDEX IF NOT EXISTS transitions_user ON transitions(user, n);
CREATE TABLE IF NOT EXISTS outbox(
  id INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT NOT NULL UNIQUE, user TEXT NOT NULL,
  seq INTEGER NOT NULL, idx INTEGER NOT NULL, kind TEXT NOT NULL, name TEXT NOT NULL,
  param TEXT, state TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT,
  result TEXT, created_ms INTEGER, done_ms INTEGER);
CREATE INDEX IF NOT EXISTS outbox_pending ON outbox(state, user, id);
CREATE TABLE IF NOT EXISTS timers(
  id INTEGER PRIMARY KEY AUTOINCREMENT, user TEXT NOT NULL, name TEXT NOT NULL,
  token INTEGER NOT NULL, ms INTEGER NOT NULL, due_ms INTEGER NOT NULL, state TEXT NOT NULL,
  delivered INTEGER NOT NULL DEFAULT 0, created_ms INTEGER, fired_ms INTEGER,
  UNIQUE(user, name, token));
CREATE INDEX IF NOT EXISTS timers_due ON timers(delivered, due_ms);
CREATE TABLE IF NOT EXISTS rejected(n INTEGER PRIMARY KEY AUTOINCREMENT, body TEXT, reason TEXT, t_ms INTEGER);
CREATE TABLE IF NOT EXISTS host_log(n INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT, user TEXT, detail TEXT);
`

// OpenDB opens the SQLite database (pure-Go modernc driver).
// Transactions started with BeginTx are BEGIN IMMEDIATE (_txlock=immediate).
func OpenDB(path string, busyMs int, synchronous string, maxConns int) (*sql.DB, error) {
	dsn := fmt.Sprintf("file:%s?_txlock=immediate&_pragma=busy_timeout(%d)&_pragma=journal_mode(WAL)&_pragma=synchronous(%s)&_pragma=foreign_keys(0)",
		path, busyMs, synchronous)
	db, err := sql.Open("sqlite", dsn)
	if err != nil {
		return nil, err
	}
	db.SetMaxOpenConns(maxConns)
	db.SetMaxIdleConns(maxConns)
	db.SetConnMaxIdleTime(0)
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	if err := db.PingContext(ctx); err != nil {
		db.Close()
		return nil, err
	}
	return db, nil
}

// IsBusy reports SQLITE_BUSY / SQLITE_LOCKED (DB unavailable: retry later, NACK).
func IsBusy(err error) bool {
	if err == nil {
		return false
	}
	var se *sqlite.Error
	if errors.As(err, &se) {
		c := se.Code() & 0xff
		return c == sqlite3.SQLITE_BUSY || c == sqlite3.SQLITE_LOCKED
	}
	s := err.Error()
	return strings.Contains(s, "database is locked") || strings.Contains(s, "SQLITE_BUSY")
}

// IsConstraint reports a constraint violation (e.g. outbox.key UNIQUE tripwire).
func IsConstraint(err error) bool {
	var se *sqlite.Error
	if errors.As(err, &se) {
		return se.Code()&0xff == sqlite3.SQLITE_CONSTRAINT
	}
	return strings.Contains(err.Error(), "constraint")
}

func initSchema(db *sql.DB) error {
	ctx := context.Background()
	for _, s := range []string{schema, domainSchema} {
		if _, err := db.ExecContext(ctx, s); err != nil {
			return err
		}
	}
	tx, err := db.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	if err := seedDomain(tx); err != nil {
		return err
	}
	for k, v := range map[string]string{"clock_ms": "0", "ignore_cancel": "0", "timer_dup": "1"} {
		if _, err := tx.Exec(`INSERT OR IGNORE INTO meta(k,v) VALUES(?,?)`, k, v); err != nil {
			return err
		}
	}
	return tx.Commit()
}

func getMeta(q interface {
	QueryRow(string, ...any) *sql.Row
}, k string) (string, bool) {
	var v string
	if err := q.QueryRow(`SELECT v FROM meta WHERE k=?`, k).Scan(&v); err != nil {
		return "", false
	}
	return v, true
}

func setMeta(tx *sql.Tx, k, v string) error {
	_, err := tx.Exec(`INSERT INTO meta(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v`, k, v)
	return err
}

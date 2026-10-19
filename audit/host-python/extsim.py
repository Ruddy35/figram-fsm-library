"""Simulated external services (CRM/helpdesk/calendar and Telegram Bot API).

Never talks to the network. State lives in its own SQLite file (ext.sqlite), committed
independently of the host DB, so it survives a kill -9 of the host exactly like a remote
service would.

* CRM: every operation carries an idempotency key. The first request with a key applies the
  effect and stores the result; later requests with the same key return the stored result
  (Stripe-like). A later request with the same key but a different fingerprint is answered
  with the stored result AND recorded in `key_conflicts` (this is how ET-01 shows up).
* Telegram: NO idempotency. Every successful request delivers a message (this is the honest
  model: Bot API sendMessage has no idempotency key), so retries after an ambiguous failure
  produce visible duplicates.

Fault modes are rows in table `modes` (written by the test runner, i.e. by "the outside world"):
  ok | 500 | 429 | timeout (effect applied, response lost) | timeout_before (not applied) |
  dup (request delivered twice by the network) | delay (ms) | reorder (batch applied shuffled)
Each row applies to the next `remaining` requests of its target (crm | tg | all).
"""
from __future__ import annotations

import hashlib
import json
import random
import sqlite3
import threading
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS modes(n INTEGER PRIMARY KEY AUTOINCREMENT, target TEXT NOT NULL,
  mode TEXT NOT NULL, remaining INTEGER NOT NULL, ms INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS requests(n INTEGER PRIMARY KEY AUTOINCREMENT, target TEXT, key TEXT,
  user TEXT, op TEXT, fp TEXT, mode TEXT, outcome TEXT, t REAL);
CREATE TABLE IF NOT EXISTS crm_effects(key TEXT PRIMARY KEY, user TEXT NOT NULL, op TEXT NOT NULL,
  fp TEXT NOT NULL, result TEXT NOT NULL, applied_n INTEGER);
CREATE TABLE IF NOT EXISTS key_conflicts(n INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT, user TEXT,
  op TEXT, fp_old TEXT, fp_new TEXT);
CREATE TABLE IF NOT EXISTS crm_orders(order_id INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT NOT NULL UNIQUE,
  user TEXT NOT NULL, items TEXT NOT NULL, address TEXT);
CREATE TABLE IF NOT EXISTS crm_tickets(ticket_id INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT NOT NULL UNIQUE,
  user TEXT NOT NULL, state TEXT NOT NULL, messages INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS crm_ticket_log(n INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT NOT NULL UNIQUE,
  ticket_id INTEGER, op TEXT, text TEXT);
CREATE TABLE IF NOT EXISTS crm_calendar(event_id INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT NOT NULL UNIQUE,
  user TEXT NOT NULL, slot TEXT);
CREATE TABLE IF NOT EXISTS crm_notify(n INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT NOT NULL UNIQUE,
  user TEXT NOT NULL, text TEXT);
CREATE TABLE IF NOT EXISTS reorders(n INTEGER PRIMARY KEY AUTOINCREMENT, size INTEGER, perm TEXT);
CREATE TABLE IF NOT EXISTS tg_deliveries(message_id INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT,
  user TEXT NOT NULL, text TEXT NOT NULL, t REAL);
"""


class Transient(Exception):
    """Retryable failure (HTTP 500/429, timeout). `applied` = effect may have happened."""

    def __init__(self, code: str, retry_after_ms: int = 0, applied: bool = False):
        super().__init__(code)
        self.code = code
        self.retry_after_ms = retry_after_ms
        self.applied = applied


class Permanent(Exception):
    """Non-retryable failure (business error, e.g. ticket not found)."""


def fingerprint(user: str, op: str, payload) -> str:
    raw = json.dumps([user, op, payload], sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def init_db(path: str) -> None:
    c = sqlite3.connect(path)
    c.execute("PRAGMA journal_mode=WAL")
    c.executescript(SCHEMA)
    c.commit()
    c.close()


def set_mode(path: str, target: str, mode: str, count: int, ms: int = 0) -> None:
    """Called by the runner (outside world)."""
    c = sqlite3.connect(path, timeout=10)
    if mode == "ok":
        c.execute("UPDATE modes SET remaining=0 WHERE target=? OR ?='all'", (target, target))
    else:
        c.execute("INSERT INTO modes(target,mode,remaining,ms) VALUES(?,?,?,?)", (target, mode, count, ms))
    c.commit()
    c.close()


class ExtSim:
    def __init__(self, path: str, seed: int = 1, reorder_k: int = 3):
        self.path = path
        self._tl = threading.local()
        self._rng = random.Random(seed)
        self._rcv = threading.Condition()
        self._batch: list = []
        self.reorder_k = reorder_k

    def _c(self) -> sqlite3.Connection:
        c = getattr(self._tl, "c", None)
        if c is None:
            c = sqlite3.connect(self.path, timeout=10, isolation_level=None)
            c.execute("PRAGMA busy_timeout=10000")
            self._tl.c = c
        return c

    def _take_mode(self, target: str):
        c = self._c()
        c.execute("BEGIN IMMEDIATE")
        try:
            row = c.execute("SELECT n, mode, ms FROM modes WHERE remaining>0 AND (target=? OR target='all') "
                            "ORDER BY n LIMIT 1", (target,)).fetchone()
            if row:
                c.execute("UPDATE modes SET remaining=remaining-1 WHERE n=?", (row[0],))
            c.execute("COMMIT")
        except BaseException:
            c.execute("ROLLBACK")
            raise
        return (row[1], row[2]) if row else ("ok", 0)

    def _log(self, target, key, user, op, fp, mode, outcome):
        self._c().execute("INSERT INTO requests(target,key,user,op,fp,mode,outcome,t) VALUES(?,?,?,?,?,?,?,?)",
                          (target, key, user, op, fp, mode, outcome, time.time()))

    # ---------------------------------------------------------------- reorder
    def _reordered(self, fn):
        with self._rcv:
            slot = {"fn": fn, "done": False, "res": None, "exc": None}
            self._batch.append(slot)
            if len(self._batch) >= self.reorder_k:
                self._flush_locked()
            deadline = time.monotonic() + 0.04
            while not slot["done"]:
                rem = deadline - time.monotonic()
                if rem <= 0:
                    self._flush_locked()
                    break
                self._rcv.wait(rem)
        if slot["exc"] is not None:
            raise slot["exc"]
        return slot["res"]

    def _flush_locked(self):
        batch, self._batch = self._batch, []
        order = list(range(len(batch)))
        self._rng.shuffle(order)
        if len(batch) > 1 and order == sorted(order):
            order.reverse()          # a batch of >1 requests is always applied out of arrival order
        if len(batch) > 1:
            self._c().execute("INSERT INTO reorders(size, perm) VALUES(?,?)", (len(batch), json.dumps(order)))
        for i in order:
            s = batch[i]
            try:
                s["res"] = s["fn"]()
            except BaseException as e:  # noqa: BLE001
                s["exc"] = e
            s["done"] = True
        self._rcv.notify_all()

    # ---------------------------------------------------------------- dispatch
    def _run(self, target, key, user, op, fp, apply):
        mode, ms = self._take_mode(target)
        if mode == "500":
            self._log(target, key, user, op, fp, mode, "500")
            raise Transient("500")
        if mode == "429":
            self._log(target, key, user, op, fp, mode, "429")
            raise Transient("429", retry_after_ms=ms or 20)
        if mode == "timeout_before":
            self._log(target, key, user, op, fp, mode, "timeout_before")
            raise Transient("timeout", applied=False)
        if mode == "delay":
            time.sleep(ms / 1000.0)
        if mode == "reorder":
            res = self._reordered(lambda: apply(mode))
        else:
            res = apply(mode)
        if mode == "dup":
            apply("dup2")  # the network delivered the same request a second time
        if mode == "timeout":
            raise Transient("timeout", applied=True)  # applied, but the response is lost
        return res

    # ---------------------------------------------------------------- CRM
    def crm(self, op: str, key: str, user: str, payload: dict) -> dict:
        fp = fingerprint(user, op, payload)
        return self._run("crm", key, user, op, fp, lambda mode: self._crm_apply(op, key, user, payload, fp, mode))

    def _crm_apply(self, op, key, user, payload, fp, mode):
        c = self._c()
        c.execute("BEGIN IMMEDIATE")
        try:
            row = c.execute("SELECT fp, result FROM crm_effects WHERE key=?", (key,)).fetchone()
            if row:
                if row[0] != fp:
                    c.execute("INSERT INTO key_conflicts(key,user,op,fp_old,fp_new) VALUES(?,?,?,?,?)",
                              (key, user, op, row[0], fp))
                self._log("crm", key, user, op, fp, mode, "dedup")
                c.execute("COMMIT")
                return json.loads(row[1])
            result = self._crm_effect(c, op, key, user, payload)
            self._log("crm", key, user, op, fp, mode, "applied")
            n = c.execute("SELECT last_insert_rowid()").fetchone()[0]
            c.execute("INSERT INTO crm_effects(key,user,op,fp,result,applied_n) VALUES(?,?,?,?,?,?)",
                      (key, user, op, fp, json.dumps(result), n))
            c.execute("COMMIT")
            return result
        except Permanent:
            c.execute("ROLLBACK")
            self._log("crm", key, user, op, fp, mode, "permanent")
            raise
        except BaseException:
            c.execute("ROLLBACK")
            raise

    def _crm_effect(self, c, op, key, user, p):
        if op == "create_order":
            c.execute("INSERT INTO crm_orders(key,user,items,address) VALUES(?,?,?,?)",
                      (key, user, json.dumps(p.get("items", [])), p.get("address")))
            return {"order_id": c.execute("SELECT last_insert_rowid()").fetchone()[0]}
        if op == "ticket_create":
            c.execute("INSERT INTO crm_tickets(key,user,state,messages) VALUES(?,?,?,1)", (key, user, "open"))
            tid = c.execute("SELECT last_insert_rowid()").fetchone()[0]
            c.execute("INSERT INTO crm_ticket_log(key,ticket_id,op,text) VALUES(?,?,?,?)", (key, tid, op, p.get("text")))
            return {"ticket_id": tid}
        if op in ("ticket_message", "ticket_escalate", "ticket_close"):
            tid = p.get("ticket_id")
            row = c.execute("SELECT state FROM crm_tickets WHERE ticket_id=? AND user=?", (tid, user)).fetchone()
            if row is None:
                raise Permanent(f"no ticket {tid} for {user}")
            if op == "ticket_message":
                c.execute("UPDATE crm_tickets SET messages=messages+1 WHERE ticket_id=?", (tid,))
            elif op == "ticket_escalate":
                c.execute("UPDATE crm_tickets SET state='escalated' WHERE ticket_id=?", (tid,))
            else:
                c.execute("UPDATE crm_tickets SET state='closed' WHERE ticket_id=?", (tid,))
            c.execute("INSERT INTO crm_ticket_log(key,ticket_id,op,text) VALUES(?,?,?,?)", (key, tid, op, p.get("text")))
            return {"ticket_id": tid}
        if op == "calendar":
            c.execute("INSERT INTO crm_calendar(key,user,slot) VALUES(?,?,?)", (key, user, p.get("slot")))
            return {"event_id": c.execute("SELECT last_insert_rowid()").fetchone()[0]}
        if op == "notify":
            c.execute("INSERT INTO crm_notify(key,user,text) VALUES(?,?,?)", (key, user, p.get("text")))
            return {"notified": True}
        raise Permanent(f"unknown op {op}")

    # ---------------------------------------------------------------- Telegram
    def tg_send(self, key: str, user: str, text: str) -> dict:
        """`key` is NOT an idempotency key for Telegram; it is only logged so the checker can
        count duplicate deliveries of the same outbox action."""
        return self._run("tg", key, user, "send", text, lambda mode: self._tg_apply(key, user, text, mode))

    def _tg_apply(self, key, user, text, mode):
        c = self._c()
        c.execute("BEGIN IMMEDIATE")
        try:
            c.execute("INSERT INTO tg_deliveries(key,user,text,t) VALUES(?,?,?,?)", (key, user, text, time.time()))
            mid = c.execute("SELECT last_insert_rowid()").fetchone()[0]
            self._log("tg", key, user, "send", text, mode, "delivered")
            c.execute("COMMIT")
        except BaseException:
            c.execute("ROLLBACK")
            raise
        return {"message_id": mid}

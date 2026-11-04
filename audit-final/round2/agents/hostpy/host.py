#!/usr/bin/env python3
"""hostpy — a production-like Figram host (engine protocol 3) built from docs/host-contract.md
and docs/format.md only (tests/engine/host_sim.py was not used to write it).

One host process = one bot document. Storage is SQLite in WAL mode:
  host.db  — snapshots, processed update ids, the inbound queue (Telegram updates, timer
             firings, outcome events, admin operations), the outbox (keys, run ids, positions,
             the seq of the reply), the scheduler (unless --sched-outside), the host's own data
             read by conditions, an event log used by the checker; all of it rolls back together
             when the database is restored from a backup;
  hwm.db   — the high-water mark of seq per user (section 4: outside the database that can roll
             back);
  sched.db — the scheduler when --sched-outside (an external job queue that does not roll back);
  metrics.db — counters of the run (not rolled back);
  tg.db    — simulated Telegram (getUpdates deliveries and the chat), external;
  ext.db   — simulated CRM, bank, venue, e-mail and notification services, external.

Threads: a getUpdates poller, a callback (webhook) poller, a scheduler, a dispatcher with
per-user claiming (one user at a time per worker), worker threads, partitioned sequential
executors (per-user commit order, the barrier is "no unexecuted sequential row of the user"),
background executors for detached actions.

Deliberately broken variants (negative controls and document traps) are selected with --flag.
"""
from __future__ import annotations

import argparse
import json
import os
import queue
import random
import signal
import sqlite3
import sys
import threading
import time
import traceback
import uuid
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("FIGRAM_REPO", "/home/user/figram-audit-worktrees/a6-hostpy")
sys.path.insert(0, os.path.join(REPO, "clients", "python"))
sys.path.insert(0, HERE)

from figram_client import Engine, EngineError, outcome_event  # noqa: E402
import protocol  # noqa: E402  (the client imports it as a top-level module)
import ext as extmod  # noqa: E402

FLAGS_KNOWN = {
    # negative controls (a broken host; some invariant must go red)
    "no-user-lock",        # two workers may process the same user at once
    "no-dedupe",           # processed update ids are not checked
    "random-keys",         # a fresh idempotency key per execution attempt
    "outcome-after-mark",  # mark the call executed, then record its outcome in a second step
    "no-reconcile",        # deliver `failed` without asking the system by key
    "bg-in-barrier",       # background actions are part of the per-user barrier
    "no-barrier",          # conditions are computed without waiting for the user's outbox
    "no-runid-check",      # outcomes and timers of a rolled-back history are not dropped
    "keys-seq",            # keys user:seq:index (host_sim style) although the db can roll back
    "store-only-if-seq",   # the snapshot is written only when seq changed
    # document-following variants that the audit questions
    "reconcile-read",      # reconciliation is a plain read by key (the contract's wording)
    "sched-pair",          # scheduler keyed by (user, name), a start replaces only a greater token
    "outcome-inline",      # the executor processes the outcome itself, under the user's lock
    # a repair the documents do not describe
    "rearm",               # at start: re-arm timers the restored snapshots hold but nobody will fire
    "purge-other-runs",    # at start: drop scheduled events whose run id is not in the restored db
    "gc-outbox",           # executed outbox rows are deleted (the usual outbox clean-up)
    "sched-replace-other-run",  # a start replaces a stored event with the same token and another run id
}
CRASH_POINTS = ["poll.after_inbound", "cb.after_inbound", "w.before_commit", "w.after_commit",
                "x.after_effect", "bg.after_effect", "s.after_fire", "x.between_mark_and_outcome",
                "w.before_commit.call", "w.before_commit.timer"]

AMOUNT = 10
BALANCE = 50
SEQ_ATTEMPTS = 3          # attempts of a sequential call before reconciling
BG_ATTEMPTS = 6           # attempts of a background action before the failure queue
ENGINE_RETRIES = 3

SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots(user TEXT PRIMARY KEY, snap TEXT NOT NULL, docv INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS processed(user TEXT, uid TEXT, at REAL, PRIMARY KEY(user, uid));
CREATE TABLE IF NOT EXISTS inbound(id INTEGER PRIMARY KEY AUTOINCREMENT, user TEXT, uid TEXT, kind TEXT,
  event TEXT, token INTEGER, run TEXT, data TEXT, at REAL);
CREATE INDEX IF NOT EXISTS inbound_user ON inbound(user, id);
CREATE TABLE IF NOT EXISTS outbox(id INTEGER PRIMARY KEY AUTOINCREMENT, user TEXT, part INTEGER, seq INTEGER,
  pos INTEGER, kind TEXT, name TEXT, token INTEGER, detached INTEGER, key TEXT UNIQUE, logical TEXT,
  src TEXT, run TEXT, ms INTEGER, data TEXT, created REAL, state TEXT DEFAULT 'new', attempts INTEGER DEFAULT 0,
  next_at REAL DEFAULT 0, executed_at REAL, result TEXT);
CREATE INDEX IF NOT EXISTS outbox_seq ON outbox(part, detached, state, user, id);
CREATE INDEX IF NOT EXISTS outbox_user ON outbox(user, detached, state);
CREATE INDEX IF NOT EXISTS outbox_call ON outbox(user, kind, name, token);
CREATE TABLE IF NOT EXISTS timers(user TEXT, name TEXT, token INTEGER, run TEXT, due REAL,
  PRIMARY KEY(user, name, token));
CREATE INDEX IF NOT EXISTS timers_due ON timers(due);
CREATE TABLE IF NOT EXISTS cart(user TEXT, key TEXT PRIMARY KEY, item TEXT);
CREATE INDEX IF NOT EXISTS cart_user ON cart(user);
CREATE TABLE IF NOT EXISTS funds(user TEXT PRIMARY KEY, spent INTEGER);
CREATE TABLE IF NOT EXISTS seats(user TEXT PRIMARY KEY, seat TEXT, extended INTEGER);
CREATE TABLE IF NOT EXISTS ledger(key TEXT PRIMARY KEY, user TEXT, kind TEXT, at REAL);
CREATE TABLE IF NOT EXISTS failq(outbox_id INTEGER PRIMARY KEY, user TEXT, reason TEXT, at REAL);
CREATE TABLE IF NOT EXISTS poison(id INTEGER PRIMARY KEY AUTOINCREMENT, user TEXT, uid TEXT, reason TEXT, at REAL);
CREATE TABLE IF NOT EXISTS elog(id INTEGER PRIMARY KEY AUTOINCREMENT, user TEXT, uid TEXT, kind TEXT, op TEXT,
  event TEXT, token INTEGER, run TEXT, seqarg INTEGER, guards TEXT, docv INTEGER, before TEXT, after TEXT,
  actions TEXT, accepted INTEGER, recovered INTEGER, note TEXT, bg_pending INTEGER, at REAL, recv REAL);
CREATE INDEX IF NOT EXISTS elog_user ON elog(user, id);
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
"""
SCHED_SCHEMA = """
CREATE TABLE IF NOT EXISTS timers(user TEXT, name TEXT, token INTEGER, run TEXT, due REAL,
  PRIMARY KEY(user, name, token));
CREATE INDEX IF NOT EXISTS timers_due ON timers(due);
"""


def connect(path: str) -> sqlite3.Connection:
    c = sqlite3.connect(path, timeout=120, isolation_level=None, check_same_thread=False)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA synchronous=NORMAL")
    c.execute("PRAGMA busy_timeout=120000")
    return c


class Tx:
    """BEGIN IMMEDIATE … COMMIT/ROLLBACK."""

    def __init__(self, c):
        self.c = c

    def __enter__(self):
        self.c.execute("BEGIN IMMEDIATE")
        return self.c

    def __exit__(self, et, ev, tb):
        if et is None:
            self.c.execute("COMMIT")
        else:
            try:
                self.c.execute("ROLLBACK")
            except sqlite3.Error:
                pass
        return False


def init_dbs(d: str, sched_outside: bool) -> None:
    c = connect(os.path.join(d, "host.db"))
    c.executescript(SCHEMA)
    c.close()
    c = connect(os.path.join(d, "hwm.db"))
    c.execute("CREATE TABLE IF NOT EXISTS hwm(user TEXT PRIMARY KEY, seq INTEGER)")
    c.close()
    c = connect(os.path.join(d, "metrics.db"))
    c.execute("CREATE TABLE IF NOT EXISTS m(k TEXT PRIMARY KEY, v REAL)")
    c.execute("CREATE TABLE IF NOT EXISTS alerts(id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT, user TEXT, "
              "detail TEXT, at REAL)")
    c.close()
    if sched_outside:
        c = connect(os.path.join(d, "sched.db"))
        c.executescript(SCHED_SCHEMA)
        c.close()


class Doc:
    """What the host needs to know about the document: declarations of the actions (to pick the
    executor and the external system) and the waiting messages (x-waiting, for the checker)."""

    def __init__(self, path: str) -> None:
        d = json.load(open(path))
        self.calls = {}
        for n, v in d.get("calls", {}).items():
            v = {"text": v} if isinstance(v, str) else v
            self.calls[n] = v
        self.waiting = {}

        def walk(name, node):
            if "children" in node:
                for k, v in node["children"].items():
                    walk(k, v)
            elif "x-waiting" in node:
                self.waiting[name] = node["x-waiting"]
        walk("bot", d["bot"])


class Crash(Exception):
    pass


class Host:
    def __init__(self, a) -> None:
        self.a = a
        self.dir = a.dir
        self.flags = set(a.flag)
        bad = self.flags - FLAGS_KNOWN
        if bad:
            raise SystemExit(f"unknown flags {bad}")
        self.docv = a.docv
        self.doc = Doc(a.doc)
        self.rng = random.Random(a.seed * 7919 + os.getpid())
        self.crash_p = a.crash_p
        self.crash_points = set(a.crash_points.split(",")) if a.crash_points else set(CRASH_POINTS)
        self.stop = threading.Event()
        self.tl = threading.local()
        self.busy: set[str] = set()
        self.busy_lock = threading.Lock()
        self.work: queue.Queue = queue.Queue()
        self.bg_claimed: set[int] = set()
        self.bg_lock = threading.Lock()
        self.mbuf: dict[str, float] = {}
        self.mlock = threading.Lock()
        self.user_locks: dict[str, threading.Lock] = {}
        engine_bin = a.engine
        self.engine = Engine(engine_bin, a.doc, processes=a.procs, timeout=10.0)
        self.engine_pids = lambda: [p._p.pid for p in self.engine._procs]

    # --- infrastructure ------------------------------------------------------------------
    def db(self) -> sqlite3.Connection:
        c = getattr(self.tl, "db", None)
        if c is None:
            c = self.tl.db = connect(os.path.join(self.dir, "host.db"))
        return c

    def sched(self) -> sqlite3.Connection:
        if not self.a.sched_outside:
            return self.db()
        c = getattr(self.tl, "sched", None)
        if c is None:
            c = self.tl.sched = connect(os.path.join(self.dir, "sched.db"))
        return c

    def hwmdb(self) -> sqlite3.Connection:
        c = getattr(self.tl, "hwm", None)
        if c is None:
            c = self.tl.hwm = connect(os.path.join(self.dir, "hwm.db"))
        return c

    def tg(self) -> sqlite3.Connection:
        c = getattr(self.tl, "tg", None)
        if c is None:
            c = self.tl.tg = connect(os.path.join(self.dir, "tg.db"))
        return c

    def ext(self) -> extmod.Ext:
        e = getattr(self.tl, "ext", None)
        if e is None:
            e = self.tl.ext = extmod.Ext(os.path.join(self.dir, "ext.db"))
        return e

    def metric(self, k: str, v: float = 1) -> None:
        with self.mlock:
            self.mbuf[k] = self.mbuf.get(k, 0) + v

    def alert(self, kind: str, user: str, detail: str) -> None:
        c = getattr(self.tl, "mdb", None)
        if c is None:
            c = self.tl.mdb = connect(os.path.join(self.dir, "metrics.db"))
        c.execute("INSERT INTO alerts(kind,user,detail,at) VALUES(?,?,?,?)", (kind, user, detail, time.time()))

    def flush_metrics(self) -> None:
        with self.mlock:
            buf, self.mbuf = self.mbuf, {}
        if not buf:
            return
        c = getattr(self.tl, "mdb", None)
        if c is None:
            c = self.tl.mdb = connect(os.path.join(self.dir, "metrics.db"))
        with Tx(c):
            for k, v in buf.items():
                c.execute("INSERT INTO m VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=v+excluded.v", (k, v))

    def crash(self, point: str) -> None:
        if self.crash_p > 0 and point in self.crash_points and self.rng.random() < self.crash_p:
            try:
                self.flush_metrics()
                self.metric("crash." + point)
                self.flush_metrics()
            except Exception:
                pass
            sys.stderr.write(f"CRASH at {point}\n")
            sys.stderr.flush()
            os._exit(70)

    def loop(self, fn, *args, idle=0.005):
        """Run fn until stop; fn returns True when it did work."""
        while not self.stop.is_set():
            try:
                did = fn(*args)
            except Exception:
                self.metric("thread_exception")
                sys.stderr.write(traceback.format_exc())
                did = False
                time.sleep(0.05)
            if not did:
                time.sleep(idle)

    # --- inbound: getUpdates and the webhook of the external systems ----------------------
    def meta(self, c, k, default="0"):
        r = c.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
        return r[0] if r else default

    def poll_tg(self) -> bool:
        c, t = self.db(), self.tg()
        off = int(self.meta(c, "tg_offset"))
        rows = t.execute("SELECT seq, update_id, user, payload FROM deliveries WHERE seq>? ORDER BY seq LIMIT 200",
                         (off,)).fetchall()
        if not rows:
            return False
        now = time.time()
        with Tx(c):
            for seq, upd, user, payload in rows:
                p = json.loads(payload)
                c.execute("INSERT INTO inbound(user,uid,kind,event,token,run,data,at) VALUES(?,?,?,?,?,?,?,?)",
                          (user, f"tg:{upd}", "tg", None, None, None, payload, now))
            c.execute("INSERT OR REPLACE INTO meta VALUES('tg_offset', ?)", (str(rows[-1][0]),))
        self.crash("poll.after_inbound")
        # getUpdates with the next offset confirms everything before it: Telegram forgets it
        with Tx(t):
            t.execute("DELETE FROM deliveries WHERE seq<=?", (rows[-1][0],))
        self.metric("tg.received", len(rows))
        return True

    def poll_callbacks(self) -> bool:
        c, e = self.db(), self.ext()
        off = int(self.meta(c, "cb_offset"))
        rows = e.c.execute("SELECT id, user, action, token, run, result, details FROM callbacks "
                           "WHERE id>? AND due<=? ORDER BY id LIMIT 200", (off, time.time())).fetchall()
        # callbacks become visible in id order only when due; stop at the first not yet due
        if not rows:
            return False
        first_not_due = e.c.execute("SELECT MIN(id) FROM callbacks WHERE id>? AND due>?", (off, time.time())).fetchone()[0]
        if first_not_due is not None:
            rows = [r for r in rows if r[0] < first_not_due]
            if not rows:
                return False
        now = time.time()
        with Tx(c):
            for cid, user, action, token, run, result, details in rows:
                c.execute("INSERT INTO inbound(user,uid,kind,event,token,run,data,at) VALUES(?,?,?,?,?,?,?,?)",
                          (user, f"call:{action}:{token}:{run}", "outcome", outcome_event(action, result), token,
                           run, json.dumps({"details": json.loads(details), "via": "callback"}), now))
            c.execute("INSERT OR REPLACE INTO meta VALUES('cb_offset', ?)", (str(rows[-1][0]),))
        self.crash("cb.after_inbound")
        self.metric("callbacks.received", len(rows))
        return True

    # --- scheduler ----------------------------------------------------------------------
    def scheduler(self) -> bool:
        s = self.sched()
        rows = s.execute("SELECT user, name, token, run FROM timers WHERE due<=? ORDER BY due LIMIT 200",
                         (time.time(),)).fetchall()
        if not rows:
            return False
        c = self.db()
        now = time.time()
        if not self.a.sched_outside:
            with Tx(c):
                for user, name, token, run in rows:
                    c.execute("INSERT INTO inbound(user,uid,kind,event,token,run,data,at) VALUES(?,?,?,?,?,?,?,?)",
                              (user, f"timer:{name}:{token}:{run}", "timer", f"timer:{name}", token, run, "{}", now))
                    c.execute("DELETE FROM timers WHERE user=? AND name=? AND token=?", (user, name, token))
        else:
            with Tx(c):
                for user, name, token, run in rows:
                    c.execute("INSERT INTO inbound(user,uid,kind,event,token,run,data,at) VALUES(?,?,?,?,?,?,?,?)",
                              (user, f"timer:{name}:{token}:{run}", "timer", f"timer:{name}", token, run, "{}", now))
            self.crash("s.after_fire")
            with Tx(s):
                for user, name, token, run in rows:
                    s.execute("DELETE FROM timers WHERE user=? AND name=? AND token=? AND run=?", (user, name, token, run))
        self.metric("timers.fired", len(rows))
        return True

    # --- dispatcher and workers ---------------------------------------------------------------
    def barrier_clear(self, c, user: str) -> bool:
        if "no-barrier" in self.flags:
            return True
        seq_pending = c.execute("SELECT 1 FROM outbox WHERE user=? AND detached=0 AND state IN ('new','failq') LIMIT 1",
                                (user,)).fetchone() is not None
        if seq_pending:
            return False
        if "bg-in-barrier" in self.flags and \
                c.execute("SELECT 1 FROM outbox WHERE user=? AND detached=1 AND state IN ('new','failq') LIMIT 1",
                          (user,)).fetchone() is not None:
            # held only by background actions (counted for the checker)
            self.metric("barrier.blocked_by_background")
            return False
        return True

    def dispatcher(self) -> bool:
        c = self.db()
        if "no-user-lock" in self.flags:
            # a host without a per-user lock: every inbound row goes to any free worker
            rows = c.execute("SELECT id, user FROM inbound ORDER BY id LIMIT 500").fetchall()
            n = 0
            for rid, u in rows:
                with self.busy_lock:
                    if ("row", rid) in self.busy:
                        continue
                    self.busy.add(("row", rid))
                self.work.put(("row", rid, u))
                n += 1
            return n > 0 and self.work.qsize() < 4 * self.a.workers
        users = [u for (u,) in c.execute("SELECT user FROM inbound GROUP BY user ORDER BY MIN(id) LIMIT 2000")]
        n = 0
        for u in users:
            with self.busy_lock:
                if u in self.busy:
                    continue
            if "outcome-inline" not in self.flags and not self.barrier_clear(c, u):
                continue
            with self.busy_lock:
                if u in self.busy:
                    continue
                self.busy.add(u)
            self.work.put(("user", None, u))
            n += 1
        return n > 0 and self.work.qsize() < 4 * self.a.workers

    def worker(self) -> None:
        while not self.stop.is_set():
            try:
                what, rid, user = self.work.get(timeout=0.05)
            except queue.Empty:
                continue
            try:
                if what == "row":
                    row = self.db().execute("SELECT id, uid, kind, event, token, run, data FROM inbound WHERE id=?",
                                            (rid,)).fetchone()
                    if row is not None and self.barrier_clear(self.db(), user):
                        self.process(user, *row)
                elif "outcome-inline" in self.flags:
                    # section 1 read literally: take the user's lock, then wait for the barrier
                    lk = self.user_locks.setdefault(user, threading.Lock())
                    with lk:
                        end = time.time() + 30
                        while not self.barrier_clear(self.db(), user) and time.time() < end:
                            time.sleep(0.005)
                        self.process_user(user)
                else:
                    self.process_user(user)
            except Exception:
                self.metric("worker_exception")
                sys.stderr.write(traceback.format_exc())
            finally:
                with self.busy_lock:
                    self.busy.discard(("row", rid) if what == "row" else user)

    def process_user(self, user: str) -> None:
        c = self.db()
        for _ in range(20):
            row = c.execute("SELECT id, uid, kind, event, token, run, data FROM inbound WHERE user=? ORDER BY id LIMIT 1",
                            (user,)).fetchone()
            if row is None or self.stop.is_set():
                return
            if not self.barrier_clear(c, user):
                return
            self.process(user, *row)

    # --- conditions (the host's data) ---------------------------------------------------------
    def cond(self, c, user: str, name: str, snap, kind, event, token, data) -> bool:
        # the barrier must hold whenever a condition is computed (counted for the checker)
        if c.execute("SELECT 1 FROM outbox WHERE user=? AND detached=0 AND state='new' LIMIT 1", (user,)).fetchone():
            self.metric("cond.with_pending_sequential")
        d = json.loads(data) if data else {}
        if name == "cart_not_empty":
            return c.execute("SELECT COUNT(*) FROM cart WHERE user=?", (user,)).fetchone()[0] > 0
        if name == "out_of_stock":
            return d.get("details", {}).get("reason") == "out_of_stock"
        if name == "has_funds":
            r = c.execute("SELECT spent FROM funds WHERE user=?", (user,)).fetchone()
            return BALANCE - (r[0] if r else 0) >= AMOUNT
        if name == "all_settled":
            pend = (snap or {}).get("pending", [])
            mine = token if kind == "outcome" and event.startswith("call:transfer:") else None
            return not any(w["call"] == "transfer" and w["token"] != mine for w in pend)
        if name == "hold_extendable":
            r = c.execute("SELECT extended FROM seats WHERE user=?", (user,)).fetchone()
            return r is not None and r[0] == 0
        if name == "seat_chosen":
            return c.execute("SELECT 1 FROM seats WHERE user=?", (user,)).fetchone() is not None
        raise KeyError(f"unknown condition {name}")

    # --- one event ------------------------------------------------------------------------------
    def event_of(self, payload: dict) -> str | None:
        """The host builds events from updates itself, with its own prefixes (section 3)."""
        t = payload.get("type")
        if t == "button":
            return "btn:" + payload["data"]
        if t == "command":
            return "cmd:" + payload["text"].lstrip("/").split()[0]
        if t == "text":
            return "msg:text"
        return None

    def drop(self, user, iid, uid, kind, note, event=None, token=None, run=None, before=None):
        c = self.db()
        with Tx(c):
            if "no-dedupe" not in self.flags:
                c.execute("INSERT OR IGNORE INTO processed VALUES(?,?,?)", (user, uid, time.time()))
            c.execute("INSERT INTO elog(user,uid,kind,op,event,token,run,docv,before,after,note,at) "
                      "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                      (user, uid, kind, "drop", event, token, run, self.docv, before, before, note, time.time()))
            c.execute("DELETE FROM inbound WHERE id=?", (iid,))
        self.metric("drop." + note)

    def engine_call(self, fn):
        for attempt in range(ENGINE_RETRIES):
            try:
                return fn()
            except EngineError as e:
                msg = str(e)
                if "protocol violation" in msg or "invalid reply" in msg or "out of order" in msg \
                        or "unknown id" in msg or "integer id" in msg:
                    self.metric("engine.protocol_violation")
                    self.alert("protocol_violation", "", msg[:300])
                if not e.retryable:
                    raise
                self.metric("engine.retry")
                time.sleep(0.05 * (attempt + 1))
        raise EngineError("engine retries exhausted", retryable=False)

    def process(self, user, iid, uid, kind, event, token, run, data) -> None:
        c = self.db()
        r = c.execute("SELECT at FROM inbound WHERE id=?", (iid,)).fetchone()
        self.tl.recv = r[0] if r else None
        if "no-dedupe" not in self.flags and \
                c.execute("SELECT 1 FROM processed WHERE user=? AND uid=?", (user, uid)).fetchone():
            with Tx(c):
                c.execute("DELETE FROM inbound WHERE id=?", (iid,))
            self.metric("dup.skipped." + kind)
            return
        srow = c.execute("SELECT snap FROM snapshots WHERE user=?", (user,)).fetchone()
        snap = json.loads(srow[0]) if srow else None
        before = srow[0] if srow else None
        if kind == "admin":
            return self.admin(user, iid, uid, event, snap, before)
        if kind == "tg":
            event = self.event_of(json.loads(data))
            if event is None or event.startswith(("timer:", "call:")):
                return self.drop(user, iid, uid, kind, "rejected_payload", before=before)
        if kind == "outcome" and "no-runid-check" not in self.flags:
            action = event[len("call:"):event.rindex(":")]
            r = c.execute("SELECT run FROM outbox WHERE user=? AND kind='call' AND name=? AND token=?",
                          (user, action, token)).fetchall()
            if [x[0] for x in r] != [run]:
                return self.drop(user, iid, uid, kind, "stale_run", event, token, run, before)
        if kind == "timer" and "no-runid-check" not in self.flags:
            name = event[len("timer:"):]
            r = c.execute("SELECT run FROM outbox WHERE user=? AND kind='start_timer' AND name=? AND token=?",
                          (user, name, token)).fetchall()
            if [x[0] for x in r] != [run]:
                return self.drop(user, iid, uid, kind, "stale_run", event, token, run, before)
        if snap is None and kind in ("timer", "outcome"):
            # an event of a lost snapshot: drop it, record its mark (section 1, step 3)
            return self.drop(user, iid, uid, kind, "no_snapshot", event, token, run, before)
        bg_pending = c.execute("SELECT COUNT(*) FROM outbox WHERE user=? AND detached=1 AND state='new'",
                               (user,)).fetchone()[0]
        used: dict[str, bool] = {}

        def guards(names):
            vals = {n: self.cond(c, user, n, snap, kind, event, token, data) for n in names}
            used.update(vals)
            return vals

        seqarg = None
        if snap is None:
            r = self.hwmdb().execute("SELECT seq FROM hwm WHERE user=?", (user,)).fetchone()
            seqarg = r[0] if r else None
            op = "start"
            resp = self.engine_call(lambda: self.engine.start(user, seqarg, event, guards))
        else:
            op = "step"
            if kind == "outcome":
                action = event[len("call:"):event.rindex(":")]
                outcome = event[event.rindex(":") + 1:]
                resp = self.engine_call(lambda: self.engine.outcome(user, snap, action, outcome, token, guards))
            else:
                resp = self.engine_call(lambda: self.engine.step(user, snap, event, guards,
                                                                  token if kind == "timer" else None))
        protocol.check_snapshot(resp["snapshot"])
        protocol.check_actions(resp["actions"])
        accepted = None
        if kind == "outcome" and snap is not None:
            action = event[len("call:"):event.rindex(":")]
            accepted = int(any(w["call"] == action and w["token"] == token for w in snap.get("pending", [])))
        src = {"tg": uid, "timer": uid, "outcome": uid}[kind]
        self.commit(user, iid, uid, kind, op, event, token, run, seqarg, used, before, resp, src, accepted,
                    bg_pending, data)

    def admin(self, user, iid, uid, op, snap, before) -> None:
        c = self.db()
        if op == "lose":
            with Tx(c):
                c.execute("DELETE FROM snapshots WHERE user=?", (user,))
                c.execute("INSERT OR IGNORE INTO processed VALUES(?,?,?)", (user, uid, time.time()))
                c.execute("INSERT INTO elog(user,uid,kind,op,docv,before,after,at) VALUES(?,?,?,?,?,?,?,?)",
                          (user, uid, "admin", "lose", self.docv, before, None, time.time()))
                c.execute("DELETE FROM inbound WHERE id=?", (iid,))
            return
        if op == "restart":
            if snap is None:
                return self.drop(user, iid, uid, "admin", "restart_without_snapshot", before=before)
            resp = self.engine_call(lambda: self.engine.restart(user, snap))
            protocol.check_snapshot(resp["snapshot"])
            protocol.check_actions(resp["actions"])
            self.commit(user, iid, uid, "admin", "restart", None, None, None, None, {}, before, resp, uid, None, 0, "{}")
            return
        raise ValueError(op)

    def key_of(self, user, src, seq, pos, aname, occ) -> str:
        if "keys-seq" in self.flags:
            return f"{user}:{seq}:{pos}"
        return f"{user}:{src}:{aname}:{occ}"

    def commit(self, user, iid, uid, kind, op, event, token, run, seqarg, used, before, resp, src, accepted,
               bg_pending, data) -> None:
        c = self.db()
        snap = resp["snapshot"]
        seq = snap.get("seq", 0)
        now = time.time()
        rows = []
        occ: dict[str, int] = {}
        for pos, a in enumerate(resp["actions"]):
            if "send" in a:
                k, name, tok = "send", a["send"], None
            elif "call" in a:
                k, name, tok = "call", a["call"], a.get("token")
            elif "start_timer" in a:
                k, name, tok = "start_timer", a["start_timer"], a["token"]
            else:
                k, name, tok = "cancel_timer", a["cancel_timer"], None
            aname = f"{k}:{name}"
            o = occ.get(aname, 0)
            occ[aname] = o + 1
            key = self.key_of(user, src, seq, pos, aname, o)
            logical = f"{user}|{src}|{aname}|{o}"
            arun = str(uuid.uuid4()) if (k == "start_timer" or (k == "call" and tok is not None)) else None
            part = zlib.crc32(user.encode()) % self.a.executors
            rows.append((user, part, seq, pos, k, name, tok, int(bool(a.get("detached"))), key, logical, src, arun,
                         a.get("ms"), data, now))
        # the high-water mark first, outside the database that can roll back (section 4)
        h = self.hwmdb()
        with Tx(h):
            h.execute("INSERT INTO hwm VALUES(?,?) ON CONFLICT(user) DO UPDATE SET seq=max(seq, excluded.seq)",
                      (user, seq))
        self.crash("w.before_commit")
        if kind == "outcome":
            self.crash("w.before_commit.call")
        if kind == "timer":
            self.crash("w.before_commit.timer")
        after = json.dumps(snap, separators=(",", ":"))
        with Tx(c):
            if "no-dedupe" not in self.flags and \
                    c.execute("SELECT 1 FROM processed WHERE user=? AND uid=?", (user, uid)).fetchone():
                self.metric("dup.race")
                return
            store = True
            if "store-only-if-seq" in self.flags and before is not None:
                store = json.loads(before).get("seq", 0) != seq
            if store:
                c.execute("INSERT INTO snapshots VALUES(?,?,?) ON CONFLICT(user) DO UPDATE SET snap=excluded.snap, "
                          "docv=excluded.docv", (user, after, self.docv))
            if "no-dedupe" not in self.flags:
                c.execute("INSERT OR IGNORE INTO processed VALUES(?,?,?)", (user, uid, now))
            for r in rows:
                try:
                    c.execute("INSERT INTO outbox(user,part,seq,pos,kind,name,token,detached,key,logical,src,run,ms,"
                              "data,created) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", r)
                except sqlite3.IntegrityError:
                    # the key exists: the outbox drops the action as a duplicate
                    self.metric("outbox.duplicate_key")
                    c.execute("INSERT INTO elog(user,uid,kind,op,note,at) VALUES(?,?,?,?,?,?)",
                              (user, uid, kind, "outbox_dup", r[8], now))
            if op == "start":
                # neither start nor the placeholder knows the old timers (section 4)
                self.sched_purge(user, seqarg)
            c.execute("INSERT INTO elog(user,uid,kind,op,event,token,run,seqarg,guards,docv,before,after,actions,"
                      "accepted,recovered,bg_pending,at,recv) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      (user, uid, kind, op, event, token, run, seqarg, json.dumps(used), self.docv, before, after,
                       json.dumps(resp["actions"]), accepted, int(bool(resp.get("recovered"))), bg_pending, now,
                       getattr(self.tl, "recv", None)))
        self.metric("steps")
        self.metric("steps." + kind)
        self.crash("w.after_commit")
        with Tx(c):
            c.execute("DELETE FROM inbound WHERE id=?", (iid,))

    def sched_purge(self, user, mark) -> None:
        if mark is None:
            return
        s = self.sched()
        if self.a.sched_outside:
            with Tx(s):
                s.execute("DELETE FROM timers WHERE user=? AND token<=?", (user, mark))
        else:
            s.execute("DELETE FROM timers WHERE user=? AND token<=?", (user, mark))

    # --- executors ------------------------------------------------------------------------------
    def seq_executor(self, part: int) -> bool:
        c = self.db()
        rows = c.execute(
            "SELECT o.id, o.user, o.seq, o.kind, o.name, o.token, o.key, o.logical, o.src, o.run, o.ms, o.data, "
            "o.created, o.state FROM outbox o JOIN (SELECT user, MIN(id) m FROM outbox WHERE part=? AND detached=0 "
            "AND state IN ('new','failq') GROUP BY user) x ON o.id=x.m ORDER BY o.id LIMIT 64", (part,)).fetchall()
        did = False
        for r in rows:
            if r[13] != "new":
                continue      # the user's queue waits for a decision on a failed action
            self.execute(*r[:13])
            did = True
        return did

    def mark_done(self, c, oid, result) -> None:
        if "gc-outbox" in self.flags:
            c.execute("DELETE FROM outbox WHERE id=?", (oid,))
            return
        c.execute("UPDATE outbox SET state='done', executed_at=?, result=? WHERE id=? AND state='new'",
                  (time.time(), json.dumps(result), oid))

    def execute(self, oid, user, seq, kind, name, token, key, logical, src, run, ms, data, created) -> None:
        c = self.db()
        if kind == "send":
            t = self.tg()
            with Tx(t):
                cur = t.execute("INSERT INTO chat(user, message, key, at) VALUES(?,?,?,?)", (user, name, key, time.time()))
            self.crash("x.after_effect")
            with Tx(c):
                self.mark_done(c, oid, {"message_id": cur.lastrowid})
            return
        if kind == "start_timer":
            due = created + ms / 1000.0       # from the step's commit (section 2)
            if self.a.sched_outside:
                s = self.sched()
                with Tx(s):
                    self.sched_start(s, user, name, token, run, due)
                self.crash("x.after_effect")
                with Tx(c):
                    self.mark_done(c, oid, {})
            else:
                with Tx(c):
                    self.sched_start(c, user, name, token, run, due)
                    self.mark_done(c, oid, {})
            return
        if kind == "cancel_timer":
            if self.a.sched_outside:
                s = self.sched()
                with Tx(s):
                    s.execute("DELETE FROM timers WHERE user=? AND name=? AND token<?", (user, name, seq))
                self.crash("x.after_effect")
                with Tx(c):
                    self.mark_done(c, oid, {})
            else:
                with Tx(c):
                    c.execute("DELETE FROM timers WHERE user=? AND name=? AND token<?", (user, name, seq))
                    self.mark_done(c, oid, {})
            return
        # call
        if name in extmod.SERVICES:
            return self.external(oid, user, name, token, key, logical, src, run, data, detached=False)
        with Tx(c):
            self.internal(c, user, name, key, data)
            self.mark_done(c, oid, {})

    def sched_start(self, s, user, name, token, run, due) -> None:
        if "sched-pair" in self.flags:
            # stored under (user, name); a start replaces the stored event only with a greater token
            cur = s.execute("SELECT MAX(token) FROM timers WHERE user=? AND name=?", (user, name)).fetchone()[0]
            if cur is not None and cur >= token:
                self.metric("sched.start_refused")
                self.alert("start_refused", user, json.dumps([name, token, cur]))
                return
            s.execute("DELETE FROM timers WHERE user=? AND name=?", (user, name))
        if "sched-replace-other-run" in self.flags:
            s.execute("DELETE FROM timers WHERE user=? AND name=? AND token=? AND run<>?", (user, name, token, run))
        s.execute("INSERT OR IGNORE INTO timers VALUES(?,?,?,?,?)", (user, name, token, run, due))

    def internal(self, c, user, name, key, data) -> None:
        d = json.loads(data) if data else {}
        if name == "add_item":
            c.execute("INSERT OR IGNORE INTO cart VALUES(?,?,?)", (user, key, d.get("item", "pizza")))
        elif name == "clear_cart":
            c.execute("DELETE FROM cart WHERE user=?", (user,))
        elif name == "reserve_seat":
            c.execute("INSERT OR REPLACE INTO seats VALUES(?,?,0)", (user, d.get("seat", "A1")))
        elif name == "extend_hold":
            c.execute("UPDATE seats SET extended=1 WHERE user=?", (user,))
        elif name == "release_seat":
            c.execute("DELETE FROM seats WHERE user=?", (user,))
        else:
            # log_status, flag_email, record_*, close_account, log_email_failure, log_slow, audit_log
            c.execute("INSERT OR IGNORE INTO ledger VALUES(?,?,?,?)", (key, user, name, time.time()))

    def payload(self, c, user, name, logical, src, run, data) -> dict:
        p = {"logical": logical, "src": src, "run": run, "user": user}
        d = json.loads(data) if data else {}
        if name == "create_order":
            p["items"] = sorted(i for (i,) in c.execute("SELECT item FROM cart WHERE user=?", (user,)))
        elif name == "transfer":
            p["amount"] = AMOUNT
        elif name == "book_seat":
            r = c.execute("SELECT seat FROM seats WHERE user=?", (user,)).fetchone()
            p["seat"] = r[0] if r else None
        return p

    def external(self, oid, user, name, token, key, logical, src, run, data, detached: bool) -> None:
        c, e = self.db(), self.ext()
        p = self.payload(c, user, name, logical, src, run, data)
        attempts = BG_ATTEMPTS if detached else SEQ_ATTEMPTS
        res = None
        last = None
        if detached:
            # one attempt per pick; the row carries its attempt count and next time
            n = c.execute("SELECT attempts FROM outbox WHERE id=?", (oid,)).fetchone()[0] + 1
            k = key if "random-keys" not in self.flags else f"{key}#{uuid.uuid4()}"
            try:
                res = e.call(name, k, user, run, token, p, n)
            except (extmod.Timeout, extmod.ServerError) as ex:
                last = ex
                if n < attempts:
                    with Tx(c):
                        c.execute("UPDATE outbox SET attempts=?, next_at=? WHERE id=?",
                                  (n, time.time() + 0.1 * 2 ** (n - 1), oid))
                    self.metric("bg.retry")
                    return
        else:
            for n in range(1, attempts + 1):
                k = key if "random-keys" not in self.flags else f"{key}#{uuid.uuid4()}"
                try:
                    res = e.call(name, k, user, run, token, p, n)
                    break
                except (extmod.Timeout, extmod.ServerError) as ex:
                    last = ex
                    self.metric("x.retry")
                    time.sleep(0.005 * n)
        outcome = details = None
        failq = False
        if res is not None:
            outcome, details = res
        else:
            # gave up without an answer
            if token is not None:
                if "no-reconcile" in self.flags:
                    r = None
                elif "reconcile-read" in self.flags:
                    r = e.lookup(name, key)
                else:
                    r = e.fence(name, key)
                self.metric("x.reconcile")
                if r is not None:
                    outcome, details = r
                    self.metric("x.reconciled_found")
                else:
                    outcome, details = "failed", {"error": str(last)}
            if detached:
                failq = True
        self.crash("bg.after_effect" if detached else "x.after_effect")
        if name == "transfer" and outcome == "sent":
            spent_inc = AMOUNT
        else:
            spent_inc = 0
        if "outcome-after-mark" in self.flags and token is not None:
            with Tx(c):
                self.apply_answer(c, user, spent_inc)
                self.mark_done(c, oid, {"outcome": outcome})
            self.crash("x.between_mark_and_outcome")
            with Tx(c):
                self.record_outcome(c, user, name, token, run, outcome, details)
            return
        if "outcome-inline" in self.flags and token is not None:
            # the reading of section 2 where the executor itself runs the outcome through section 1:
            # it takes the user's lock (held by a worker that waits for this very row: the barrier)
            lk = self.user_locks.setdefault(user, threading.Lock())
            if lk.acquire(timeout=3):
                lk.release()
            else:
                # the worker holds the lock and waits at the barrier for this very row
                self.metric("deadlock.outcome_inline")
                self.alert("deadlock", user, f"executor waited 3 s for the user's lock to record {name}:{token}")
        with Tx(c):
            if spent_inc:
                self.apply_answer(c, user, spent_inc)
            if failq:
                c.execute("UPDATE outbox SET state='failq', attempts=?, executed_at=?, result=? WHERE id=?",
                          (attempts, time.time(), json.dumps({"outcome": outcome, "error": str(last)}), oid))
                c.execute("INSERT OR IGNORE INTO failq VALUES(?,?,?,?)", (oid, user, str(last), time.time()))
            else:
                self.mark_done(c, oid, {"outcome": outcome, "details": details})
            if token is not None:
                self.record_outcome(c, user, name, token, run, outcome, details)
        if failq:
            self.alert("failure_queue", user, f"{name} {key}: {last}")

    def apply_answer(self, c, user, spent_inc) -> None:
        if spent_inc:
            c.execute("INSERT INTO funds VALUES(?,?) ON CONFLICT(user) DO UPDATE SET spent=spent+excluded.spent",
                      (user, spent_inc))

    def record_outcome(self, c, user, name, token, run, outcome, details) -> None:
        c.execute("INSERT INTO inbound(user,uid,kind,event,token,run,data,at) VALUES(?,?,?,?,?,?,?,?)",
                  (user, f"call:{name}:{token}:{run}", "outcome", outcome_event(name, outcome), token, run,
                   json.dumps({"details": details or {}, "via": "executor"}), time.time()))

    def bg_executor(self) -> bool:
        c = self.db()
        rows = c.execute("SELECT id, user, seq, kind, name, token, key, logical, src, run, ms, data, created FROM outbox "
                         "WHERE detached=1 AND state='new' AND next_at<=? ORDER BY id LIMIT 32", (time.time(),)).fetchall()
        did = False
        for r in rows:
            with self.bg_lock:
                if r[0] in self.bg_claimed:
                    continue
                self.bg_claimed.add(r[0])
            try:
                oid, user, seq, kind, name, token, key, logical, src, run, ms, data, created = r
                cur = c.execute("SELECT state, next_at FROM outbox WHERE id=?", (oid,)).fetchone()
                if cur is None or cur[0] != "new" or cur[1] > time.time():
                    continue        # executed or rescheduled by another thread since the select
                if name in extmod.SERVICES:
                    self.external(oid, user, name, token, key, logical, src, run, data, detached=True)
                else:
                    with Tx(c):
                        self.internal(c, user, name, key, data)
                        self.mark_done(c, oid, {})
                did = True
            finally:
                with self.bg_lock:
                    self.bg_claimed.discard(r[0])
        return did

    # --- life cycle --------------------------------------------------------------------------------
    def restore_repair(self) -> None:
        """After a restore of host.db relative to an external scheduler: (1) drop scheduled events
        whose run id is not that of the restored start_timer row; (2) re-arm every timer that the
        restored snapshot still holds, whose firing is neither processed nor queued, and that the
        scheduler does not hold (it fired in the lost history and its firing was rolled back)."""
        c, s = self.db(), self.sched()
        runs = {(u, n, t): (r, cr, ms) for u, n, t, r, cr, ms in c.execute(
            "SELECT user, name, token, run, created, ms FROM outbox WHERE kind='start_timer'")}
        dropped = rearmed = 0
        with Tx(s) if self.a.sched_outside else Tx(c):
            for u, n, t, r in s.execute("SELECT user, name, token, run FROM timers").fetchall():
                if "purge-other-runs" in self.flags and runs.get((u, n, t), (None,))[0] != r:
                    s.execute("DELETE FROM timers WHERE user=? AND name=? AND token=? AND run=?", (u, n, t, r))
                    dropped += 1
        for u, snap in c.execute("SELECT user, snap FROM snapshots").fetchall():
            if "rearm" not in self.flags:
                break
            for n, t in json.loads(snap).get("timers", {}).items():
                row = runs.get((u, n, t))
                if row is None:
                    continue
                r, cr, ms = row
                uid = f"timer:{n}:{t}:{r}"
                if c.execute("SELECT 1 FROM processed WHERE user=? AND uid=?", (u, uid)).fetchone() or \
                        c.execute("SELECT 1 FROM inbound WHERE user=? AND uid=?", (u, uid)).fetchone() or \
                        s.execute("SELECT 1 FROM timers WHERE user=? AND name=? AND token=?", (u, n, t)).fetchone():
                    continue
                with Tx(s) if self.a.sched_outside else Tx(c):
                    s.execute("INSERT OR IGNORE INTO timers VALUES(?,?,?,?,?)", (u, n, t, r, cr + ms / 1000.0))
                rearmed += 1
        self.metric("repair.dropped", dropped)
        self.metric("repair.rearmed", rearmed)

    def run(self) -> None:
        threads = []
        if self.flags & {"rearm", "purge-other-runs"}:
            self.restore_repair()

        def spawn(fn, *args, idle=0.005):
            t = threading.Thread(target=self.loop, args=(fn, *args), kwargs={"idle": idle}, daemon=True)
            t.start()
            threads.append(t)

        spawn(self.poll_tg, idle=0.01)
        spawn(self.poll_callbacks, idle=0.02)
        spawn(self.scheduler, idle=0.01)
        spawn(self.dispatcher, idle=0.003)
        for _ in range(self.a.workers):
            t = threading.Thread(target=self.worker, daemon=True)
            t.start()
            threads.append(t)
        for p in range(self.a.executors):
            spawn(self.seq_executor, p, idle=0.004)
        for _ in range(self.a.bg):
            spawn(self.bg_executor, idle=0.01)
        with open(os.path.join(self.dir, "host.pids"), "w") as f:
            f.write(json.dumps({"host": os.getpid(), "engines": self.engine_pids()}))
        signal.signal(signal.SIGTERM, lambda *_: self.stop.set())
        while not self.stop.is_set():
            time.sleep(0.25)
            try:
                self.flush_metrics()
                with open(os.path.join(self.dir, "host.pids"), "w") as f:
                    f.write(json.dumps({"host": os.getpid(), "engines": self.engine_pids()}))
            except Exception:
                pass
        for t in threads:
            t.join(5)
        self.flush_metrics()
        self.engine.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    ap.add_argument("--doc", required=True)
    ap.add_argument("--docv", type=int, default=1)
    ap.add_argument("--engine", default=os.path.join(REPO, ".lake/build/bin/figram-engine"))
    ap.add_argument("--procs", type=int, default=2)
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--executors", type=int, default=8)
    ap.add_argument("--bg", type=int, default=3)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--crash-p", type=float, default=0.0)
    ap.add_argument("--crash-points", default="")
    ap.add_argument("--sched-outside", action="store_true")
    ap.add_argument("--flag", action="append", default=[])
    a = ap.parse_args()
    init_dbs(a.dir, a.sched_outside)
    Host(a).run()


if __name__ == "__main__":
    main()

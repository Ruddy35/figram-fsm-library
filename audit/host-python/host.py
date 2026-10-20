#!/usr/bin/env python3
"""Production-like Figram host in Python (stdlib + sqlite3). The engine runs as child
processes (`figram-engine`) behind our own transport (engine_transport.py).

Process protocol with the test runner ("Telegram" + operator), one JSON object per line:
  in : {"t":"update","d":N,"body":<update JSON>} | {"t":"update","d":N,"text":"<raw text>"}
       {"t":"ctl","d":N,"c":{...}}
  out: {"t":"ready","pid":P} | {"t":"ack","d":N,"status":S,"lat_us":..,"step_us":..}
       {"t":"nack","d":N,"why":..} | {"t":"done","d":N,"r":{...}}

Design (see /home/user/figram-audit/hosts/shared/README.md and domain.md):
* per-user lock + optimistic `version` column; different users in parallel (thread pool);
* step: read snapshot -> engine step -> on need: drain own pending calls (G1), take resource
  locks (G2), compute guards from the domain DB -> retry -> ONE transaction (snapshot, processed
  update id, transition log, outbox rows, timer table) -> ack;
* idempotency key = "<user>:<seq>:<index>"; seq is strictly monotone over the user's lifetime:
  `start` without `seq` only for a user with no row; every reset/repair uses `start` with
  `seq` = the persistent high-water seq (FORMAT.md §7, HOST.md §4; immune to ET-01/P-01);
  outbox.key is UNIQUE (a collision aborts, never dedups);
* outbox executor threads; local calls are applied in the same transaction that marks the row
  done (exactly-once); external calls carry the key to the CRM (effectively-once); `send` is
  at-least-once (Telegram has no idempotency key);
* timers: own scheduler on a simulated clock persisted in the DB; update id of a timer fire is
  "timer:<name>:<token>"; tokens are unique per user because seq never resets.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import queue
import re
import signal
import sqlite3
import sys
import threading
import time
import uuid
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import domain  # noqa: E402
from engine_transport import (EngineFailure, EnginePool, EngineReplyError, Faults,  # noqa: E402
                              valid_snapshot)
from extsim import ExtSim, Permanent, Transient  # noqa: E402

MAX_INT = 9007199254740991
USER_RE = re.compile(r"[A-Za-z0-9_\-]{1,64}")
EVENT_RE = re.compile(r"(cmd|btn|msg|payment):[a-z0-9_]{1,48}")

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS users(user TEXT PRIMARY KEY, family TEXT NOT NULL, doc TEXT NOT NULL,
  snapshot TEXT, version INTEGER NOT NULL, hw_seq INTEGER NOT NULL CHECK(hw_seq>=0),
  created_ms INTEGER, updated_ms INTEGER);
CREATE TABLE IF NOT EXISTS processed(user TEXT NOT NULL, update_id TEXT NOT NULL, result TEXT NOT NULL,
  t_ms INTEGER, PRIMARY KEY(user, update_id));
CREATE TABLE IF NOT EXISTS transitions(n INTEGER PRIMARY KEY AUTOINCREMENT, user TEXT NOT NULL,
  update_id TEXT, event TEXT, arg TEXT, token INTEGER, kind TEXT, seq_before INTEGER, seq INTEGER,
  at TEXT, doc TEXT, actions TEXT, recovered INTEGER, t_ms INTEGER, guards TEXT, note TEXT,
  changed INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS outbox(id INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT NOT NULL UNIQUE,
  user TEXT NOT NULL, seq INTEGER, idx INTEGER, kind TEXT NOT NULL, name TEXT, action TEXT, param TEXT,
  state TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT, result TEXT,
  created_ms INTEGER, done_ms INTEGER);
CREATE INDEX IF NOT EXISTS outbox_pending ON outbox(state, user, id);
CREATE TABLE IF NOT EXISTS timers(id INTEGER PRIMARY KEY AUTOINCREMENT, user TEXT NOT NULL, name TEXT NOT NULL,
  token INTEGER NOT NULL, due_ms INTEGER NOT NULL, state TEXT NOT NULL, created_ms INTEGER,
  delivered INTEGER NOT NULL DEFAULT 0, fired_ms INTEGER);
CREATE INDEX IF NOT EXISTS timers_due ON timers(delivered, due_ms);
CREATE TABLE IF NOT EXISTS rejected(n INTEGER PRIMARY KEY AUTOINCREMENT, body TEXT, reason TEXT);
CREATE TABLE IF NOT EXISTS host_log(n INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT, user TEXT, detail TEXT);
"""

BROKEN_VARIANTS = {
    "no-dedup": "update_id dedup disabled",
    "ack-before-commit": "ack sent before the step transaction commits",
    "outbox-after-ack": "snapshot committed and acked, outbox rows written in a later transaction",
    "const-guards": "every guard is constant true",
    "no-drain": "guards computed without draining the user's pending calls (no rule G1)",
    "no-resource-lock": "naive scarce-resource guard: no lock, counts executed reservations only (no rule G2)",
    "seq-keys-start": "reset via `start` + deletion of the user's outbox history (ET-01 path)",
    "random-keys": "external idempotency key gets a fresh nonce on every attempt",
    "no-user-lock": "no per-user lock and no optimistic version check",
    "stale-token-lookup": "timer token taken from the current snapshot instead of the fired timer",
    "no-validate": "Telegram updates are not validated (forged timer:* events pass, token from body)",
    "memory-timers": "scheduler state is not durable (timers lost on host restart)",
    "no-capacity-check": "reserve_slot trusts the guard (no capacity check in the domain write)",
    "no-ack-retry": "a DB error after a successful external effect re-executes the effect later",
    "no-crash-points": "crash ctl is accepted but never armed (test-of-the-test)",
    "trust-snapshot": "stored snapshot passed to the engine as-is (no schema/high-water check, no repair)",
}


def no_duplicate_keys(pairs):
    """Reject {"user":"a","user":"b"}: different parsers would pick different users."""
    d = {}
    for k, v in pairs:
        if k in d:
            raise ValueError(f"duplicate key {k!r}")
        d[k] = v
    return d


class Nack(Exception):
    pass


class VersionConflict(Exception):
    pass


class DrainFailed(Exception):
    pass


class Reject(Exception):
    pass


class Host:
    def __init__(self, a):
        self.a = a
        self.broken = set(filter(None, (a.broken or "").split(",")))
        unknown = self.broken - set(BROKEN_VARIANTS)
        if unknown:
            raise SystemExit(f"unknown broken variant(s): {unknown}")
        self._tl = threading.local()
        self.out_lock = threading.Lock()
        self.locks_lock = threading.Lock()
        self.ulocks: dict[str, threading.Lock] = defaultdict(threading.Lock)
        self.olocks: dict[str, threading.Lock] = defaultdict(threading.Lock)
        self.rlocks: dict[str, threading.Lock] = defaultdict(threading.Lock)
        self.faults = Faults()
        self.pools: dict[str, EnginePool] = {}
        self.pools_lock = threading.Lock()
        self.crash_armed: list[tuple[str, str | None]] = []
        self.crash_lock = threading.Lock()
        self.inflight = 0
        self.inflight_cv = threading.Condition()
        self.workers = ThreadPoolExecutor(a.workers, thread_name_prefix="w")
        self.ext = ExtSim(a.ext, seed=a.seed)
        self.stats = defaultdict(int)
        self.stats_lock = threading.Lock()
        c = self.conn()
        c.executescript(SCHEMA)
        c.executescript(domain.SCHEMA)
        c.execute("BEGIN IMMEDIATE")
        domain.seed(c)
        c.execute("INSERT OR IGNORE INTO meta(k,v) VALUES('clock_ms','0')")
        c.execute("INSERT OR IGNORE INTO meta(k,v) VALUES('ignore_cancel','0')")
        c.execute("INSERT OR IGNORE INTO meta(k,v) VALUES('timer_dup','1')")
        c.execute("INSERT OR IGNORE INTO meta(k,v) VALUES('ctl_seq','0')")
        c.execute("COMMIT")
        self.clock = int(self.meta("clock_ms"))
        if "memory-timers" in self.broken:
            # BROKEN: timers were only in memory -> a restart loses them
            c.execute("UPDATE timers SET state='lost', delivered=1 WHERE delivered=0")
        self.executor = Executor(self, a.executors)

    # ------------------------------------------------------------ infra
    def conn(self) -> sqlite3.Connection:
        c = getattr(self._tl, "c", None)
        if c is None:
            c = sqlite3.connect(self.a.db, timeout=self.a.busy_ms / 1000.0, isolation_level=None,
                                check_same_thread=False)
            c.execute("PRAGMA journal_mode=WAL")
            c.execute(f"PRAGMA synchronous={self.a.sync}")
            c.execute(f"PRAGMA busy_timeout={self.a.busy_ms}")
            self._tl.c = c
        return c

    def meta(self, k):
        r = self.conn().execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
        return r[0] if r else None

    def set_meta(self, c, k, v):
        c.execute("INSERT INTO meta(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, str(v)))

    def emit(self, obj):
        line = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
        with self.out_lock:
            sys.stdout.write(line + "\n")
            sys.stdout.flush()

    def stat(self, k, n=1):
        with self.stats_lock:
            self.stats[k] += n

    def log(self, kind, user, detail):
        with contextlib.suppress(sqlite3.Error):
            self.conn().execute("INSERT INTO host_log(kind,user,detail) VALUES(?,?,?)",
                                (kind, user, json.dumps(detail, ensure_ascii=False)))

    def lock_of(self, table, key):
        with self.locks_lock:
            return table[key]

    def crash_point(self, at: str, tag: str):
        """Fault injection: a REAL kill -9 of this host process at the armed point."""
        with self.crash_lock:
            for i, (a, on) in enumerate(self.crash_armed):
                if a == at and (on is None or on == tag or (on.endswith("*") and tag.startswith(on[:-1]))):
                    del self.crash_armed[i]
                    sys.stderr.write(f"CRASH at={at} tag={tag} pid={os.getpid()}\n")
                    sys.stderr.flush()
                    with self.out_lock:   # tell the runner which armed point fired, then die
                        sys.stdout.write(json.dumps({"t": "crashing", "at": a, "on": on, "tag": tag}) + "\n")
                        sys.stdout.flush()
                        os.kill(os.getpid(), signal.SIGKILL)

    def doc_path(self, doc):
        p = os.path.join(self.a.bots, f"{doc}.bot.json")
        if not os.path.isfile(p):
            raise RuntimeError(f"no such bot document {doc}")
        return p

    def engine(self, doc) -> EnginePool:
        with self.pools_lock:
            p = self.pools.get(doc)
            if p is None:
                p = self.pools[doc] = EnginePool(self.a.engine, self.doc_path(doc), self.a.engines,
                                                 self.a.engine_timeout, self.faults, self.a.engine_proxy)
            return p

    def family_doc(self, family):
        return self.meta(f"doc:{family}") or family

    def timer_names(self, doc):
        names = set()

        def walk(node):
            names.update((node.get("timers") or {}).keys())
            for ch in (node.get("children") or {}).values():
                walk(ch)
        walk(json.load(open(self.doc_path(doc)))["bot"])
        return names

    # ------------------------------------------------------------ updates
    def validate(self, body):
        if type(body) is not dict:
            raise Reject("update is not an object")
        user = body.get("user")
        if type(user) is not str or not USER_RE.fullmatch(user):
            raise Reject("bad user")
        uid = body.get("update_id")
        if type(uid) is not int or not 0 <= uid <= MAX_INT:
            raise Reject("bad update_id")
        ev = body.get("event")
        arg = body.get("arg")
        if "no-validate" in self.broken:
            if type(ev) is not str or not ev:
                raise Reject("bad event")
            tok = body.get("token")
            return user, uid, ev, arg if isinstance(arg, str) else None, tok if type(tok) is int else None
        if type(ev) is not str or not EVENT_RE.fullmatch(ev):
            raise Reject("bad event (timer:* and unknown prefixes are not accepted from Telegram)")
        if "token" in body:
            raise Reject("token is not accepted from Telegram")
        if arg is not None and (type(arg) is not str or len(arg) > 4096 or "\x00" in arg):
            raise Reject("bad arg")
        return user, uid, ev, arg, None

    def on_update(self, d, body=None, text=None):
        t0 = time.perf_counter()
        acked = []

        def ack(status, step_us=0):
            if not acked:
                acked.append(1)
                self.emit({"t": "ack", "d": d, "status": status,
                           "lat_us": int((time.perf_counter() - t0) * 1e6), "step_us": step_us})
        try:
            if text is not None:
                try:
                    body = json.loads(text, object_pairs_hook=no_duplicate_keys)
                except ValueError as e:
                    raise Reject(f"update is not valid JSON: {e}")
            try:
                user, uid, ev, arg, tok = self.validate(body)
            except Reject as e:
                self.reject(body if text is None else text, str(e))
                self.stat("rejected")
                ack("rejected")
                return
            status, step_us = self.run_step(user, f"tg:{uid}", ev, arg, tok, early_ack=ack)
            ack(status, step_us)
        except Reject as e:
            self.reject(text, str(e))
            ack("rejected")
        except (Nack, sqlite3.OperationalError, DrainFailed, EngineFailure) as e:
            self.stat("nack")
            if not acked:
                self.emit({"t": "nack", "d": d, "why": f"{type(e).__name__}: {e}"})
        except Exception as e:  # noqa: BLE001 — never kill the worker; the update is redelivered
            self.stat("nack_internal")
            sys.stderr.write(f"internal error d={d}: {e!r}\n")
            if not acked:
                self.emit({"t": "nack", "d": d, "why": f"internal {type(e).__name__}: {e}"})
        finally:
            with self.inflight_cv:
                self.inflight -= 1
                self.inflight_cv.notify_all()

    def reject(self, body, reason):
        txt = body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)
        for _ in range(20):
            try:
                self.conn().execute("INSERT INTO rejected(body,reason) VALUES(?,?)", (txt[:2000], reason))
                return
            except sqlite3.OperationalError:
                time.sleep(0.05)

    def submit(self, fn, *args):
        with self.inflight_cv:
            self.inflight += 1
        self.workers.submit(fn, *args)

    # ------------------------------------------------------------ step
    def run_step(self, user, uid, event, arg, token, early_ack=None, timer_row=None):
        lock = (contextlib.nullcontext() if "no-user-lock" in self.broken else self.lock_of(self.ulocks, user))
        with lock:
            t1 = time.perf_counter()
            last = None
            for _ in range(8):
                try:
                    st = self._step_once(user, uid, event, arg, token, early_ack, timer_row)
                    return st, int((time.perf_counter() - t1) * 1e6)
                except EngineFailure as e:
                    self.stat("engine_retry")
                    self.log("engine_retry", user, {"uid": uid, "error": str(e)[:200]})
                    last = e
                except VersionConflict as e:
                    self.stat("version_conflict")
                    last = e
            raise Nack(f"retries exhausted: {last}")

    def parse_snapshot(self, txt, hw):
        try:
            s = json.loads(txt) if txt is not None else None
        except ValueError:
            return None, "snapshot is not JSON"
        if "trust-snapshot" in self.broken:
            return s, None                     # BROKEN: whatever is stored goes to the engine
        err = valid_snapshot(s)
        if err:
            return None, err
        if s.get("seq", 0) != hw:
            return None, f"snapshot seq {s.get('seq', 0)} != committed high-water seq {hw}"
        return s, None

    def _step_once(self, user, uid, event, arg, token, early_ack, timer_row):
        c = self.conn()
        nodedup = "no-dedup" in self.broken
        if not nodedup and c.execute("SELECT 1 FROM processed WHERE user=? AND update_id=?",
                                     (user, uid)).fetchone():
            self.stat("dup")
            return "dup"
        row = c.execute("SELECT family, doc, snapshot, version, hw_seq FROM users WHERE user=?",
                        (user,)).fetchone()
        upd = {"arg": arg, "event": event,
               # BROKEN no-resource-lock = naive host: no lock AND the guard reads executed rows only
               "count_pending_intents": "no-resource-lock" not in self.broken}
        held = []
        guards_used = None
        note = None
        try:
            if row is None:
                family = self.a.family
                doc = self.family_doc(family)
                snap, version, seq_before = None, None, 0
                r = self.engine(doc).call(user, {"op": "start"})
                kind = "start"
            else:
                family, doc, snap_txt, version, hw = row
                snap, bad = self.parse_snapshot(snap_txt, hw)
                seq_before = hw
                if bad and "trust-snapshot" in self.broken:
                    return self._commit_error(c, user, uid, bad)
                if bad:
                    r, kind, note = self._repair(user, doc, hw, bad)
                else:
                    req = {"op": "step", "snapshot": snap, "event": event}
                    if token is not None:
                        req["token"] = token
                        if "stale-token-lookup" in self.broken and event.startswith("timer:"):
                            req["token"] = snap.get("timers", {}).get(event[6:], token)
                    kind = "step"
                    try:
                        r = self.engine(doc).call(user, req)
                    except EngineReplyError as e:
                        if e.code == "bad_snapshot" and "trust-snapshot" not in self.broken:
                            r, kind, note = self._repair(user, doc, hw, f"engine: {e}")
                        else:
                            return self._commit_error(c, user, uid, str(e))
                    if "need" in r:
                        need = r["need"]
                        if "no-drain" not in self.broken:
                            self.drain_user(user)                      # rule G1
                        if "no-resource-lock" not in self.broken:      # rule G2
                            res = sorted({x for n in need for x in domain.guard_resources(n, c, user)})
                            for name in res:
                                lk = self.lock_of(self.rlocks, name)
                                lk.acquire()
                                held.append(lk)
                        guards_used = self.compute_guards(need, c, user, upd)
                        try:
                            r = self.engine(doc).call(user, {**req, "guards": guards_used})
                        except EngineReplyError as e:      # deterministic: consume, never a poison loop
                            return self._commit_error(c, user, uid, str(e))
                        if "need" in r:
                            raise EngineFailure(f"engine asked for guards twice: {r['need']}")
            if any(a.get("call") and getattr(domain.CALLS.get(a["call"]), "param", None) == "selection"
                   for a in r["actions"]) and guards_used is None and "no-drain" not in self.broken:
                self.drain_user(user)  # commit-time params read the domain: same rule as guards
            return self._commit(c, user, row, family, doc, snap, version, seq_before, r, kind, uid, event,
                                arg, token, guards_used, upd, early_ack, timer_row, note)
        finally:
            for lk in held:
                lk.release()

    def _repair(self, user, doc, hw, why):
        """Unreadable / inconsistent snapshot: start the user again from the persistent high-water
        seq (`start` with `seq`; never `start` without it: that would reuse seq values -> ET-01)."""
        self.stat("repair")
        self.log("repair", user, {"why": why, "hw": hw})
        r = self.engine(doc).call(user, {"op": "start", "seq": hw})
        return r, "repair", why

    def compute_guards(self, need, c, user, upd):
        if "const-guards" in self.broken:
            return {n: True for n in need}
        out = {}
        for n in need:
            fn = domain.GUARDS.get(n)
            if fn is None:
                raise Nack(f"no guard function for {n}")
            out[n] = bool(fn(c, user, upd))
        return out

    def _commit_error(self, c, user, uid, err):
        """Deterministic engine error for this update: consume it (no poison loop), log it."""
        c.execute("BEGIN IMMEDIATE")
        try:
            c.execute("INSERT OR REPLACE INTO processed(user,update_id,result,t_ms) VALUES(?,?,?,?)",
                      (user, uid, f"engine_error:{err}"[:300], self.clock))
            c.execute("COMMIT")
        except BaseException:
            c.execute("ROLLBACK")
            raise
        self.stat("engine_error")
        self.log("engine_error", user, {"uid": uid, "error": err})
        return "error"

    def _commit(self, c, user, row, family, doc, snap, version, seq_before, r, kind, uid, event, arg, token,
                guards_used, upd, early_ack, timer_row, note):
        new = r["snapshot"]
        seq_new = new.get("seq", 0)
        acts = r["actions"]
        changed = kind != "step" or new != snap or bool(acts)
        nodedup = "no-dedup" in self.broken
        split_outbox = "outbox-after-ack" in self.broken
        if "ack-before-commit" in self.broken and early_ack:
            early_ack("ok" if changed else "ignored")       # BROKEN: ack before the commit
        c.execute("BEGIN IMMEDIATE")
        try:
            if not nodedup and c.execute("SELECT 1 FROM processed WHERE user=? AND update_id=?",
                                         (user, uid)).fetchone():
                c.execute("ROLLBACK")
                return "dup"
            now = self.clock
            snap_txt = json.dumps(new, ensure_ascii=False, separators=(",", ":"))
            if row is None:
                try:
                    c.execute("INSERT INTO users(user,family,doc,snapshot,version,hw_seq,created_ms,updated_ms) "
                              "VALUES(?,?,?,?,1,?,?,?)", (user, family, doc, snap_txt, seq_new, now, now))
                except sqlite3.IntegrityError:
                    raise VersionConflict("user created concurrently")
            elif "no-user-lock" in self.broken:
                c.execute("UPDATE users SET snapshot=?, version=version+1, hw_seq=?, updated_ms=? WHERE user=?",
                          (snap_txt, seq_new, now, user))
            else:
                cur = c.execute("UPDATE users SET snapshot=?, version=version+1, hw_seq=MAX(hw_seq,?), updated_ms=? "
                                "WHERE user=? AND version=?", (snap_txt, seq_new, now, user, version))
                if cur.rowcount != 1:
                    raise VersionConflict("version changed")
            status = ("recovered" if r.get("recovered") else kind if kind in ("repair",) else
                      "ok" if changed else "ignored")
            c.execute("INSERT OR REPLACE INTO processed(user,update_id,result,t_ms) VALUES(?,?,?,?)",
                      (user, uid, status, now))
            if changed or guards_used is not None:
                # every committed step that changed something OR evaluated guards is logged
                # (changed=0 rows let the checker verify guards of ignored steps too)
                c.execute("INSERT INTO transitions(user,update_id,event,arg,token,kind,seq_before,seq,at,doc,actions,"
                          "recovered,t_ms,guards,note,changed) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                          (user, uid, event, arg, token, kind, seq_before, seq_new, new["at"], doc,
                           json.dumps(acts, ensure_ascii=False), 1 if r.get("recovered") else 0, now,
                           json.dumps(guards_used) if guards_used is not None else None, note,
                           1 if changed else 0))
            if kind == "repair":
                c.execute("UPDATE timers SET state='canceled' WHERE user=? AND state='scheduled'", (user,))
            if timer_row is not None:
                c.execute("UPDATE timers SET delivered=1, fired_ms=?, state=CASE WHEN state='scheduled' "
                          "THEN 'fired' ELSE state END WHERE id=?", (now, timer_row))
            if not split_outbox:
                self._insert_outbox(c, user, seq_new, acts, upd)
            self.crash_point("before_commit", event)
            c.execute("COMMIT")
        except BaseException:
            with contextlib.suppress(sqlite3.Error):
                c.execute("ROLLBACK")
            raise
        if r.get("recovered"):
            self.stat("recovered")
            self.log("recovered", user, {"uid": uid, "doc": doc})
        if split_outbox:
            if early_ack:
                early_ack(status)                            # BROKEN: ack before outbox is durable
            self.crash_point("after_commit", event)
            c.execute("BEGIN IMMEDIATE")
            self._insert_outbox(c, user, seq_new, acts, upd)
            c.execute("COMMIT")
        else:
            self.crash_point("after_commit", event)
        if acts:
            self.executor.notify(user)
        return status

    def key(self, user, seq, i):
        return f"{user}:{seq}:{i}"

    def _insert_outbox(self, c, user, seq, acts, upd):
        now = self.clock
        for i, a in enumerate(acts):
            key = self.key(user, seq, i)
            if "call" in a:
                call = domain.CALLS.get(a["call"])
                param = domain.resolve_param(call, c, user, upd) if call else None
                c.execute("INSERT INTO outbox(key,user,seq,idx,kind,name,action,param,state,created_ms) "
                          "VALUES(?,?,?,?,'call',?,?,?,'pending',?)",
                          (key, user, seq, i, a["call"], json.dumps(a), param, now))
            elif "send" in a:
                c.execute("INSERT INTO outbox(key,user,seq,idx,kind,name,action,state,created_ms) "
                          "VALUES(?,?,?,?,'send',?,?,'pending',?)", (key, user, seq, i, a["send"], json.dumps(a), now))
            elif "cancel_timer" in a:
                c.execute("UPDATE timers SET state='canceled' WHERE user=? AND name=? AND state='scheduled'",
                          (user, a["cancel_timer"]))
                c.execute("INSERT INTO outbox(key,user,seq,idx,kind,name,action,state,created_ms,done_ms) "
                          "VALUES(?,?,?,?,'cancel_timer',?,?,'done',?,?)",
                          (key, user, seq, i, a["cancel_timer"], json.dumps(a), now, now))
            elif "start_timer" in a:
                c.execute("UPDATE timers SET state='canceled' WHERE user=? AND name=? AND state='scheduled'",
                          (user, a["start_timer"]))
                c.execute("INSERT INTO timers(user,name,token,due_ms,state,created_ms) VALUES(?,?,?,?,'scheduled',?)",
                          (user, a["start_timer"], a["token"], now + a["ms"], now))
                c.execute("INSERT INTO outbox(key,user,seq,idx,kind,name,action,state,created_ms,done_ms) "
                          "VALUES(?,?,?,?,'start_timer',?,?,'done',?,?)",
                          (key, user, seq, i, a["start_timer"], json.dumps(a), now, now))

    # ------------------------------------------------------------ outbox (shared by executor and G1)
    def drain_user(self, user):
        """Rule G1: execute this user's pending outbox prefix up to its last pending call, in order,
        before guards are computed. A transient failure aborts the step (nack -> redelivery)."""
        c = self.conn()
        r = c.execute("SELECT MAX(id) FROM outbox WHERE user=? AND state='pending' AND kind='call'",
                      (user,)).fetchone()
        if r[0] is None:
            return
        self.stat("g1_drain")
        with self.lock_of(self.olocks, user):
            for attempt in range(6):
                res = self.run_outbox(user, upto=r[0])
                if res is True:
                    return
                time.sleep(min(0.2, (res or 10) / 1000.0))
        raise DrainFailed(f"pending calls of {user} could not be executed")

    def run_outbox(self, user, upto=None):
        """Execute pending call/send rows of `user` in id order. Caller holds the user's outbox lock.
        Returns True when done, or a backoff in ms after a transient failure."""
        c = self.conn()
        q = ("SELECT id,key,kind,name,param,attempts FROM outbox WHERE user=? AND state='pending' "
             "AND kind IN ('call','send')" + (" AND id<=?" if upto else "") + " ORDER BY id")
        rows = c.execute(q, (user, upto) if upto else (user,)).fetchall()
        for row in rows:
            res = self.exec_row(c, user, row)
            if res is not True:
                return res
        return True

    def _transient(self, c, rid, attempts, err, retry_ms=0):
        self.stat(f"ext_{err}")
        with contextlib.suppress(sqlite3.Error):
            c.execute("UPDATE outbox SET attempts=attempts+1, last_error=? WHERE id=?", (err, rid))
        return max(retry_ms, min(200, 5 * (2 ** min(attempts, 6))))

    def exec_row(self, c, user, row):
        rid, key, kind, name, param, attempts = row
        tag = f"call:{name}" if kind == "call" else "send"
        self.crash_point("before_effect", tag)
        try:
            if kind == "call":
                call = domain.CALLS.get(name)
                if call is None:
                    return self._fail(c, rid, f"unknown call {name}")
                if call.kind == "local":
                    c.execute("BEGIN IMMEDIATE")
                    try:
                        st = c.execute("SELECT state FROM outbox WHERE id=?", (rid,)).fetchone()
                        if st[0] != "pending":
                            c.execute("ROLLBACK")
                            return True
                        try:
                            domain.apply_local(c, name, user, key, param,
                                               check_capacity="no-capacity-check" not in self.broken)
                        except domain.PermanentCallError as e:
                            c.execute("UPDATE outbox SET state='failed', last_error=? WHERE id=?", (str(e), rid))
                            c.execute("COMMIT")
                            self.stat("outbox_failed")
                            self.log("outbox_failed", user, {"key": key, "error": str(e)})
                            return True
                        self.crash_point("after_effect_before_ack", tag)   # inside the txn: rolled back
                        c.execute("UPDATE outbox SET state='done', done_ms=?, attempts=attempts+1 WHERE id=?",
                                  (self.clock, rid))
                        c.execute("COMMIT")
                    except BaseException:
                        with contextlib.suppress(sqlite3.Error):
                            c.execute("ROLLBACK")
                        raise
                    self.stat("exec_local")
                    return True
                payload = domain.external_payload(c, name, user, param)
                ext_key = key if "random-keys" not in self.broken else f"{key}#{uuid.uuid4().hex[:8]}"
                t0 = time.monotonic()
                try:
                    result = self.ext.crm(call.op, ext_key, user, payload)
                except Transient as e:
                    return self._transient(c, rid, attempts, e.code, e.retry_after_ms)
                except Permanent as e:
                    return self._fail(c, rid, f"permanent: {e}")
                if time.monotonic() - t0 > self.a.ext_timeout:
                    return self._transient(c, rid, attempts, "client_timeout")
                self.crash_point("after_effect_before_ack", tag)

                def mark():
                    c.execute("BEGIN IMMEDIATE")
                    try:
                        st = c.execute("SELECT state FROM outbox WHERE id=?", (rid,)).fetchone()
                        if st[0] == "pending":
                            domain.apply_external_result(c, name, user, key, param, payload, result)
                            c.execute("UPDATE outbox SET state='done', done_ms=?, attempts=attempts+1, result=? "
                                      "WHERE id=?", (self.clock, json.dumps(result), rid))
                        c.execute("COMMIT")
                    except BaseException:
                        with contextlib.suppress(sqlite3.Error):
                            c.execute("ROLLBACK")
                        raise
                self.persist_ack(mark)
                self.stat("exec_external")
                return True
            # send: Telegram, at-least-once
            t0 = time.monotonic()
            try:
                result = self.ext.tg_send(key, user, name)
            except Transient as e:
                return self._transient(c, rid, attempts, "tg_" + e.code, e.retry_after_ms)
            if time.monotonic() - t0 > self.a.ext_timeout:
                return self._transient(c, rid, attempts, "tg_client_timeout")
            self.crash_point("after_effect_before_ack", tag)
            self.persist_ack(lambda: c.execute(
                "UPDATE outbox SET state='done', done_ms=?, attempts=attempts+1, result=? WHERE id=? "
                "AND state='pending'", (self.clock, json.dumps(result), rid)))
            self.stat("exec_send")
            return True
        except sqlite3.OperationalError as e:  # DB unavailable: retry later (effect may be repeated)
            self.stat("exec_db_error")
            return min(200, 20 * (attempts + 1)) if "locked" in str(e) or "busy" in str(e) else 100

    def persist_ack(self, fn, budget_s=30.0):
        """The external effect already happened: keep retrying the local 'done' write while the DB is
        busy instead of re-executing the effect (which would duplicate a Telegram message). Only if
        the DB stays unavailable for `budget_s` (or the process dies) is the effect re-executed later."""
        if "no-ack-retry" in self.broken:
            budget_s = 0.0
        end = time.monotonic() + budget_s
        delay = 0.01
        while True:
            try:
                fn()
                return
            except sqlite3.OperationalError:
                self.stat("ack_write_retry")
                if time.monotonic() > end:
                    raise
                time.sleep(delay)
                delay = min(0.2, delay * 2)

    def _fail(self, c, rid, err):
        c.execute("UPDATE outbox SET state='failed', last_error=? WHERE id=?", (err, rid))
        self.stat("outbox_failed")
        return True

    # ------------------------------------------------------------ timers
    def advance_to(self, target):
        """Fire every due timer in (due, user, name, token) order on the simulated clock. Each fire
        goes through the normal step path with update id timer:<name>:<token>."""
        c = self.conn()
        stuck = 0
        while True:
            ic = self.meta("ignore_cancel") == "1"
            states = "('scheduled','canceled')" if ic else "('scheduled')"
            rows = c.execute(f"SELECT id,user,name,token,due_ms FROM timers WHERE delivered=0 AND due_ms<=? "
                             f"AND state IN {states} ORDER BY due_ms,user,name,token,id", (target,)).fetchall()
            if not rows:
                break
            t = rows[0][4]
            batch = [r for r in rows if r[4] == t]
            if t > self.clock:
                self.clock = t
                c.execute("BEGIN IMMEDIATE")
                self.set_meta(c, "clock_ms", t)
                c.execute("COMMIT")
            ok = self.deliver_timers(batch, int(self.meta("timer_dup") or 1))
            if not ok:
                stuck += 1
                time.sleep(0.05)
                if stuck > 200:
                    raise RuntimeError("timer delivery keeps failing")
        if target > self.clock:
            self.clock = target
            c.execute("BEGIN IMMEDIATE")
            self.set_meta(c, "clock_ms", target)
            c.execute("COMMIT")

    def deliver_timers(self, rows, dup=1, uid_suffix=""):
        per_user = defaultdict(list)
        for r in rows:
            per_user[r[1]].append(r)
        results = []
        lk = threading.Lock()

        def run(user_rows):
            for rid, user, name, token, due in user_rows:
                uid = f"timer:{name}:{token}{uid_suffix}"
                try:
                    st, _ = self.run_step(user, uid, f"timer:{name}", None, token, timer_row=rid)
                    self.conn().execute("UPDATE timers SET delivered=1 WHERE id=?", (rid,))
                    with lk:
                        results.append(True)
                    self.stat(f"timer_{st}")
                except Exception as e:  # noqa: BLE001
                    sys.stderr.write(f"timer delivery failed {uid}: {e!r}\n")
                    with lk:
                        results.append(False)
        futs = []
        for user, urows in per_user.items():
            for _ in range(dup):
                futs.append(self.workers.submit(run, urows))
        for f in futs:
            f.result()
        return all(results)

    # ------------------------------------------------------------ ctl
    def barrier(self, wait_outbox=True, timeout=120):
        end = time.monotonic() + timeout
        with self.inflight_cv:
            while self.inflight > 0:
                if not self.inflight_cv.wait(max(0.0, end - time.monotonic())):
                    raise RuntimeError("barrier timeout (in-flight updates)")
        c = self.conn()
        while wait_outbox and not self.executor.paused.is_set():
            n = c.execute("SELECT COUNT(*) FROM outbox WHERE state='pending'").fetchone()[0]
            if n == 0 and self.executor.busy == 0:
                break
            if time.monotonic() > end:
                raise RuntimeError(f"barrier timeout ({n} pending outbox rows)")
            self.executor.kick_all()
            time.sleep(0.01)

    def ctl(self, d, cmd):
        k = cmd.get("ctl")
        r = {}
        if k == "barrier":
            self.barrier()
        elif k == "crash":
            if "no-crash-points" not in self.broken:
                with self.crash_lock:
                    self.crash_armed.append((cmd["at"], cmd.get("on")))
        elif k == "advance_to":
            self.barrier()
            self.advance_to(int(cmd["t"]))
            self.barrier()
            r["clock"] = self.clock
        elif k == "upgrade":
            self.barrier()
            r = self.upgrade(cmd["bot"])
        elif k == "executor":
            if cmd["mode"] == "pause":
                self.executor.paused.set()
            else:
                self.executor.paused.clear()
                self.executor.kick_all()
        elif k == "timer_mode":
            c = self.conn()
            c.execute("BEGIN IMMEDIATE")
            if "dup" in cmd:
                self.set_meta(c, "timer_dup", int(cmd["dup"]))
            if "ignore_cancel" in cmd:
                self.set_meta(c, "ignore_cancel", 1 if cmd["ignore_cancel"] else 0)
            c.execute("COMMIT")
        elif k == "replay_stale":
            self.barrier()
            rows = self.conn().execute(
                "SELECT id,user,name,token,due_ms FROM timers WHERE user=? AND state IN ('canceled','orphaned','fired') "
                "ORDER BY id", (cmd["user"],)).fetchall()
            self.deliver_timers(rows, 1)
            r["replayed"] = len(rows)
            self.barrier()
        elif k == "reset_user":
            self.barrier()
            r["status"] = self.reset_user(cmd["user"], d)
            self.barrier()
        elif k == "engine_fault":
            self.faults.arm(cmd["mode"])
        elif k == "stats":
            r = {"stats": dict(self.stats), "faults": {"injected": self.faults.injected,
                 "detected": self.faults.detected, "respawns": self.faults.respawns}}
        elif k == "stop":
            self.barrier()
            r = {"stats": dict(self.stats), "faults": {"injected": self.faults.injected,
                 "detected": self.faults.detected, "respawns": self.faults.respawns}}
            self.emit({"t": "done", "d": d, "r": r})
            self.shutdown()
            os._exit(0)
        else:
            raise RuntimeError(f"unknown ctl {k}")
        self.emit({"t": "done", "d": d, "r": r})

    def upgrade(self, new_doc):
        family = new_doc.split(".")[0]
        self.engine(new_doc)  # the document must load in the engine, otherwise keep the old one
        names = self.timer_names(new_doc)
        c = self.conn()
        c.execute("BEGIN IMMEDIATE")
        try:
            n_users = c.execute("UPDATE users SET doc=? WHERE family=?", (new_doc, family)).rowcount
            self.set_meta(c, f"doc:{family}", new_doc)
            # ET-02 mitigation: scheduled timers whose name no longer exists in the new document
            # would be accepted/ignored unpredictably -> cancel them in the scheduler.
            q = ",".join("?" * len(names)) or "''"
            n_orphan = c.execute(f"UPDATE timers SET state='orphaned' WHERE state='scheduled' AND name NOT IN ({q}) "
                                 "AND user IN (SELECT user FROM users WHERE family=?)", (*names, family)).rowcount
            c.execute("COMMIT")
        except BaseException:
            c.execute("ROLLBACK")
            raise
        self.log("upgrade", None, {"doc": new_doc, "users": n_users, "orphaned_timers": n_orphan})
        return {"users": n_users, "orphaned_timers": n_orphan}

    def reset_user(self, user, d):
        """'Delete my data, keep the account'. Correct: `start` with `seq` = the high-water seq +
        cancel every scheduled timer of the user (HOST.md §4).
        BROKEN seq-keys-start: `start` without `seq` + delete outbox history."""
        with self.lock_of(self.ulocks, user):
            c = self.conn()
            row = c.execute("SELECT family, doc, snapshot, version, hw_seq FROM users WHERE user=?", (user,)).fetchone()
            if row is None:
                return "no_user"
            family, doc, snap_txt, version, hw = row
            snap, bad = self.parse_snapshot(snap_txt, hw)
            if "seq-keys-start" in self.broken:
                r = self.engine(doc).call(user, {"op": "start"})
            else:
                r = self.engine(doc).call(user, {"op": "start", "seq": hw})
            uid = f"reset:{d}"
            new = r["snapshot"]
            c.execute("BEGIN IMMEDIATE")
            try:
                domain.reset_user_data(c, family, user)
                if "seq-keys-start" in self.broken:
                    c.execute("DELETE FROM outbox WHERE user=? AND state<>'pending'", (user,))
                    c.execute("UPDATE users SET snapshot=?, version=version+1, hw_seq=? WHERE user=?",
                              (json.dumps(new), new.get("seq", 0), user))
                else:
                    c.execute("UPDATE timers SET state='canceled' WHERE user=? AND state='scheduled'", (user,))
                    c.execute("UPDATE users SET snapshot=?, version=version+1, hw_seq=MAX(hw_seq,?) WHERE user=?",
                              (json.dumps(new), new.get("seq", 0), user))
                c.execute("INSERT OR REPLACE INTO processed(user,update_id,result,t_ms) VALUES(?,?,?,?)",
                          (user, uid, "reset", self.clock))
                c.execute("INSERT INTO transitions(user,update_id,event,arg,token,kind,seq_before,seq,at,doc,actions,"
                          "recovered,t_ms,guards,note) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                          (user, uid, "reset", None, None, "reset", hw, new.get("seq", 0), new["at"], doc,
                           json.dumps(r["actions"]), 0, self.clock, None, bad))
                self._insert_outbox(c, user, new.get("seq", 0), r["actions"], {"arg": None})
                c.execute("COMMIT")
            except BaseException:
                c.execute("ROLLBACK")
                raise
        self.executor.notify(user)
        return "ok"

    def shutdown(self):
        self.executor.stop()
        for p in list(self.pools.values()):
            p.close()


class Executor:
    """Outbox executor: E threads; one user's rows strictly in order (per-user outbox lock)."""

    def __init__(self, host: Host, n: int):
        self.h = host
        self.q: queue.Queue = queue.Queue()
        self.queued: set = set()
        self.lock = threading.Lock()
        self.paused = threading.Event()
        self.stopping = False
        self.busy = 0
        self.threads = [threading.Thread(target=self.loop, daemon=True, name=f"ex{i}") for i in range(n)]
        for t in self.threads:
            t.start()
        threading.Thread(target=self.sweep, daemon=True, name="sweep").start()

    def notify(self, user):
        with self.lock:
            if user in self.queued:
                return
            self.queued.add(user)
        self.q.put(user)

    def kick_all(self):
        try:
            for (u,) in self.h.conn().execute("SELECT DISTINCT user FROM outbox WHERE state='pending'").fetchall():
                self.notify(u)
        except sqlite3.Error:
            pass

    def sweep(self):
        while not self.stopping:
            time.sleep(0.2)
            if not self.paused.is_set():
                self.kick_all()

    def loop(self):
        while not self.stopping:
            user = self.q.get()
            with self.lock:
                self.queued.discard(user)
                self.busy += 1
            try:
                while self.paused.is_set() and not self.stopping:
                    time.sleep(0.01)
                with self.h.lock_of(self.h.olocks, user):
                    res = self.h.run_outbox(user)
                if res is not True:
                    threading.Timer(res / 1000.0, self.notify, (user,)).start()
            except Exception as e:  # noqa: BLE001
                sys.stderr.write(f"executor error {user}: {e!r}\n")
                threading.Timer(0.1, self.notify, (user,)).start()
            finally:
                with self.lock:
                    self.busy -= 1

    def stop(self):
        self.stopping = True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--ext", required=True)
    ap.add_argument("--bots", required=True)
    ap.add_argument("--engine", required=True)
    ap.add_argument("--family", required=True, help="bot family for new users (pizza, booking, ...)")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--engines", type=int, default=2)
    ap.add_argument("--executors", type=int, default=4)
    ap.add_argument("--engine-timeout", type=float, default=3.0)
    ap.add_argument("--engine-proxy", default=None)
    ap.add_argument("--ext-timeout", type=float, default=1.0)
    ap.add_argument("--busy-ms", type=int, default=3000)
    ap.add_argument("--sync", default="FULL")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--broken", default="")
    a = ap.parse_args()
    h = Host(a)
    h.advance_to(h.clock)          # timers that were due before a crash fire now (callbacks after restart)
    h.emit({"t": "ready", "pid": os.getpid(), "broken": sorted(h.broken)})
    ctlq: queue.Queue = queue.Queue()

    def ctl_loop():
        while True:
            d, cmd = ctlq.get()
            try:
                h.ctl(d, cmd)
            except Exception as e:  # noqa: BLE001
                h.emit({"t": "done", "d": d, "r": {"error": f"{type(e).__name__}: {e}"}})

    threading.Thread(target=ctl_loop, daemon=True).start()
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            m = json.loads(line)
        except ValueError:
            continue
        if m.get("t") == "update":
            if "text" in m:
                h.submit(h.on_update, m["d"], None, m["text"])
            else:
                h.submit(h.on_update, m["d"], m.get("body"))
        elif m.get("t") == "ctl":
            ctlq.put((m["d"], m["c"]))
    ctlq.put((-1, {"ctl": "stop"}))
    threading.Event().wait(60)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""C2 (cross-verify, independent model): getUpdates acknowledges cumulatively.

Telegram Bot API semantics modelled (from the Bot API docs as I know them; NO network
access in this audit, so not re-checked against the live docs):
  getUpdates(offset): "An update is considered confirmed as soon as getUpdates is
  called with an offset higher than its update_id."  Confirmed updates are forgotten.
  I.e. the acknowledgement is a PREFIX (cumulative), not per update.
  (Webhooks are different: each update is acknowledged by its own HTTP 2xx.)

Host follows HOST.md §1.1 (users in parallel, one at a time per user), §1.2 dedup,
§1.4 atomic commit, §1.5 "only after commit the host confirms the update".  With
getUpdates the only way to confirm an update is offset = update_id + 1, which
confirms every earlier update too.

Scenario: user A's update 1001 = payment:success (call create_order) is slow to commit
(slow DB / CRM-side check before commit); user B's update 1002 commits quickly.
The host acks 1002 after its commit; then the host process crashes before A commits.
After restart getUpdates(offset) no longer returns 1001.

  NAIVE  ack: offset = max(offset, update_id + 1)          -> A's payment is lost
  WATERMARK ack (control): offset = smallest update_id still in flight (or max+1) -> A redelivered

Usage: c2_cumulative_ack.py ENGINE PIZZA_DOC ; exit 0 = naive loses A, watermark does not.
"""
import os
import sys
import threading

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "ffi", "python-example"))
from figram_client import Engine  # noqa: E402


class FakeTelegram:
    """getUpdates with cumulative confirmation by offset."""

    def __init__(self, updates):
        self.queue = list(updates)            # (update_id, user, event), ascending ids
        self.lock = threading.Lock()

    def get_updates(self, offset):
        with self.lock:
            if offset is not None:
                self.queue = [u for u in self.queue if u[0] >= offset]   # confirm prefix
            return list(self.queue)


class DB:
    def __init__(self):
        self.lock = threading.Lock()
        self.users = {}       # user -> {"snapshot", "seen"}
        self.outbox = []


class Crashed(Exception):
    pass


class Host:
    """One host process; `crash()` simulates kill -9 (in-memory state is lost)."""

    def __init__(self, tg, db, engine, mode, slow_users=()):
        self.tg, self.db, self.engine, self.mode = tg, db, engine, mode
        self.offset = None
        self.in_flight = set()
        self.max_seen = None
        self.slow = {u: threading.Event() for u in slow_users}
        self.dead = False
        self.lock = threading.Lock()

    def _ack(self, uid):
        with self.lock:
            self.in_flight.discard(uid)
            if self.mode == "naive":
                self.offset = max(self.offset or 0, uid + 1)
            else:  # low watermark
                self.offset = min(self.in_flight) if self.in_flight else (self.max_seen + 1)
            off = self.offset
        if not self.dead:
            self.tg.get_updates(off)       # next long poll carries the new offset

    def handle(self, upd):
        uid, user, event = upd
        with self.db.lock:
            rec = self.db.users.get(user)
            if rec and uid in rec["seen"]:
                dup = True
            else:
                dup = False
        if dup:
            self._ack(uid)
            return
        rec = rec or {"snapshot": None, "seen": set()}
        if rec["snapshot"] is None:
            r = self.engine.start(user)
        else:
            r = self.engine.step(user, rec["snapshot"], event, lambda ns: {n: True for n in ns})
        if user in self.slow:
            self.slow[user].wait(5)       # slow pre-commit work (DB latency, lock wait...)
        if self.dead:
            raise Crashed()               # process died before commit
        with self.db.lock:
            self.db.users[user] = {"snapshot": r["snapshot"], "seen": rec["seen"] | {uid}}
            seq = r["snapshot"].get("seq", 0)
            for i, a in enumerate(r["actions"]):
                self.db.outbox.append((f"{user}:{seq}:{i}", a))
        self._ack(uid)

    def poll_once_parallel(self):
        batch = self.tg.get_updates(self.offset)
        with self.lock:
            for u in batch:
                self.in_flight.add(u[0])
                self.max_seen = max(self.max_seen or 0, u[0])
        threads = []
        for u in batch:
            t = threading.Thread(target=self._safe, args=(u,), daemon=True)
            t.start()
            threads.append(t)
        return threads

    def _safe(self, u):
        try:
            self.handle(u)
        except Crashed:
            pass

    def crash(self):
        self.dead = True
        for ev in self.slow.values():
            ev.set()


def run(engine_bin, doc, mode):
    eng = Engine(engine_bin, doc)
    db = DB()
    # Put A at "pay" and B at "cart" first (sequentially, all committed).
    def walk(user, events):
        snap = None
        for ev in events:
            r = eng.start(user) if snap is None else eng.step(user, snap, ev, lambda ns: {n: True for n in ns})
            snap = r["snapshot"]
        db.users[user] = {"snapshot": snap, "seen": set()}
    walk("A", ["/start", "btn:order", "btn:pizza", "btn:size", "btn:add", "btn:checkout", "msg:text"])
    walk("B", ["/start", "btn:order", "btn:cart"])
    assert db.users["A"]["snapshot"]["at"] == "pay", db.users["A"]
    tg = FakeTelegram([(1001, "A", "payment:success"), (1002, "B", "btn:checkout")])
    h1 = Host(tg, db, eng, mode, slow_users=("A",))
    threads = h1.poll_once_parallel()
    threads[1].join(5)                  # B committed and acked
    acked_offset = h1.offset
    h1.crash()                          # kill -9 while A has not committed
    threads[0].join(5)
    # restart: new process, offset unknown locally (fresh start uses no offset)
    h2 = Host(tg, db, eng, mode)
    redelivered = tg.get_updates(None)
    for t in h2.poll_once_parallel():
        t.join(5)
    a_at = db.users["A"]["snapshot"]["at"]
    orders = [k for k, a in db.outbox if a.get("call") == "create_order"]
    eng.close()
    print(f"[{mode}] after B's commit host offset={acked_offset}; after restart getUpdates returned "
          f"{[u[0] for u in redelivered]}; A at={a_at!r}; create_order in outbox: {orders}")
    return a_at, orders


def main():
    engine_bin, doc = sys.argv[1], sys.argv[2]
    print(f"engine: {engine_bin}")
    a1, o1 = run(engine_bin, doc, "naive")
    a2, o2 = run(engine_bin, doc, "watermark")
    ok = a1 == "pay" and o1 == [] and a2 == "done" and len(o2) == 1
    print("C2 REPRODUCED IN MODEL: naive post-commit ack loses A's paid update; watermark ack keeps it"
          if ok else "C2 NOT REPRODUCED")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()

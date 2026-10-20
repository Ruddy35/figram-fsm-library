#!/usr/bin/env python3
"""C1 (cross-verify, independent): guards are read before the asynchronous outbox
has executed the previous step's `call`s of the SAME user.

The host below follows HOST.md literally:
  §1.1 per-user lock; §1.2 dedup by update_id; §1.3 step, on `need` compute guards
  from the host's data "of the same version as the snapshot" and repeat; §1.4 one
  atomic commit of snapshot + seen + outbox (keys user:seq:i); §1.5 ack after commit;
  §2 a SEPARATE executor takes outbox entries in order, executes, marks them done,
  idempotency key = action key.
The only free parameter is how long a `call` takes in the executor (CRM latency)
compared with the gap between two updates of the same user.

Scenario P (pizza): ... btn:add (call add_to_cart) then btn:checkout 50 ms later.
   Guard cart_not_empty = cart[user] > 0.  Designer intent: address.
Scenario B (balance bot, own document): balance 100, item costs 100, user taps
   btn:buy twice (two updates 50 ms apart).  Designer intent: one grant, then no_money.

Usage: c1_guard_before_outbox.py ENGINE PIZZA_DOC SCRATCH_DIR
Exit 0 = the claim reproduced (wrong branch with a slow executor) AND controls are green
(synchronous executor gives the designer's branch).
"""
import json
import os
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "ffi", "python-example"))
from figram_client import Engine  # noqa: E402

BALANCE_DOC = {
    "figram": 1,
    "conditions": {"has_balance": "На балансе не меньше 100"},
    "calls": {"debit_100": "Списать 100 с баланса", "grant_item": "Выдать товар"},
    "bot": {"initial": "shop", "children": {
        "shop": {"on": {"btn:buy": [
            {"if": "has_balance", "to": "bought", "do": ["debit_100", "grant_item"]},
            {"to": "no_money"}]}},
        "bought": {"on": {"btn:buy": [
            {"if": "has_balance", "to": "bought", "do": ["debit_100", "grant_item"]},
            {"to": "no_money"}]}},
        "no_money": {}}},
}


class Host:
    def __init__(self, engine, call_latency):
        self.engine = engine
        self.call_latency = call_latency
        self.db_lock = threading.Lock()
        self.users = {}            # user -> {"snapshot", "seen"}
        self.outbox = []           # dicts: key, user, action, done
        self.app = {"cart": {}, "balance": {}, "granted": {}, "executed_keys": set()}
        self.locks = {}
        self.cv = threading.Condition(self.db_lock)
        self.stop = False
        self.trace = []
        self.executor = threading.Thread(target=self._executor, daemon=True)
        self.executor.start()

    # --- §1 -------------------------------------------------------------
    def guards(self, user):
        def compute(names):
            with self.db_lock:      # read host data at the moment of the step
                cart = self.app["cart"].get(user, 0)
                bal = self.app["balance"].get(user, 0)
            vals = {"cart_not_empty": cart > 0, "in_delivery_zone": True, "has_balance": bal >= 100}
            self.trace.append(("guards", user, {n: vals[n] for n in names}, {"cart": cart, "balance": bal}))
            return {n: vals[n] for n in names}
        return compute

    def handle(self, user, update_id, event):
        with self.locks.setdefault(user, threading.Lock()):            # 1.1
            with self.db_lock:
                rec = self.users.get(user, {"snapshot": None, "seen": set()})
                rec = {"snapshot": rec["snapshot"], "seen": set(rec["seen"])}
            if update_id in rec["seen"]:                                # 1.2
                return "duplicate"
            if rec["snapshot"] is None:
                r = self.engine.start(user)
            else:                                                       # 1.3
                r = self.engine.step(user, rec["snapshot"], event, self.guards(user))
            with self.db_lock:                                          # 1.4 atomic
                rec["snapshot"] = r["snapshot"]
                rec["seen"].add(update_id)
                self.users[user] = rec
                for i, a in enumerate(r["actions"]):
                    self.outbox.append({"key": f"{user}:{r['snapshot']['seq'] if 'seq' in r['snapshot'] else 0}:{i}",
                                        "user": user, "action": a, "done": False})
                self.cv.notify_all()
            self.trace.append(("commit", user, update_id, event, r["snapshot"]["at"]))
            return "ack"                                                # 1.5 ack after commit

    # --- §2 -------------------------------------------------------------
    def _executor(self):
        while True:
            with self.cv:
                while not self.stop and not any(not e["done"] for e in self.outbox):
                    self.cv.wait(0.05)
                if self.stop:
                    return
                e = next(e for e in self.outbox if not e["done"])
            a = e["action"]
            if "call" in a:
                time.sleep(self.call_latency)          # CRM / payment latency
                with self.db_lock:
                    if e["key"] not in self.app["executed_keys"]:   # idempotency key
                        self.app["executed_keys"].add(e["key"])
                        u = e["user"]
                        if a["call"] == "add_to_cart":
                            self.app["cart"][u] = self.app["cart"].get(u, 0) + 1
                        elif a["call"] == "debit_100":
                            self.app["balance"][u] = self.app["balance"].get(u, 0) - 100
                        elif a["call"] == "grant_item":
                            self.app["granted"][u] = self.app["granted"].get(u, 0) + 1
            with self.db_lock:
                e["done"] = True

    def wait_drained(self, limit=30):
        t0 = time.time()
        while time.time() - t0 < limit:
            with self.db_lock:
                if all(e["done"] for e in self.outbox):
                    return
            time.sleep(0.01)

    def drain_and_stop(self, limit=30):
        t0 = time.time()
        while time.time() - t0 < limit:
            with self.db_lock:
                if all(e["done"] for e in self.outbox):
                    break
            time.sleep(0.01)
        self.stop = True


def deliver(host, user, events, gap):
    for i, ev in enumerate(events):
        host.handle(user, i, ev)
        time.sleep(gap)


def pizza(engine_bin, doc, call_latency, gap=0.05):
    eng = Engine(engine_bin, doc)
    h = Host(eng, call_latency)
    deliver(h, "u1", ["/start", "btn:order", "btn:pizza", "btn:size"], gap)
    h.wait_drained()          # everything before btn:add has been executed
    h.handle("u1", 4, "btn:add")
    time.sleep(gap)
    h.handle("u1", 5, "btn:checkout")
    at = h.users["u1"]["snapshot"]["at"]
    h.drain_and_stop()
    eng.close()
    g = [t for t in h.trace if t[0] == "guards"]
    return at, g, h.app["cart"].get("u1", 0)


def balance(engine_bin, scratch, call_latency, gap=0.05):
    path = os.path.join(scratch, "balance.bot.json")
    with open(path, "w") as f:
        json.dump(BALANCE_DOC, f, ensure_ascii=False)
    eng = Engine(engine_bin, path)
    h = Host(eng, call_latency)
    h.app["balance"]["u1"] = 100
    h.handle("u1", 0, "/start")
    h.wait_drained()
    h.handle("u1", 1, "btn:buy")
    time.sleep(gap)
    h.handle("u1", 2, "btn:buy")
    at = h.users["u1"]["snapshot"]["at"]
    h.drain_and_stop()
    eng.close()
    g = [t for t in h.trace if t[0] == "guards"]
    return at, g, h.app["balance"]["u1"], h.app["granted"].get("u1", 0)


def main():
    engine_bin, doc, scratch = sys.argv[1], sys.argv[2], sys.argv[3]
    os.makedirs(scratch, exist_ok=True)
    ok = True
    print(f"engine: {engine_bin}")
    for lat, label in ((0.0, "CONTROL: executor fast (0 ms per call) "),
                       (0.3, "LITERAL: executor 300 ms per call, user gap 50 ms")):
        at, g, cart = pizza(engine_bin, doc, lat)
        print(f"[pizza] {label}: after btn:add, btn:checkout -> at={at!r}; guards seen={g[-1][2]} host data then={g[-1][3]}; final cart={cart}")
        if lat == 0.0:
            ok &= at == "address"
        else:
            ok &= at == "catalog"
    for lat, label in ((0.0, "CONTROL: executor fast (0 ms per call) "),
                       (0.3, "LITERAL: executor 300 ms per call, user gap 50 ms")):
        at, g, bal, granted = balance(engine_bin, scratch, lat)
        print(f"[balance] {label}: two btn:buy -> at={at!r}; guards per step={[x[2] for x in g]}; "
              f"final balance={bal}; items granted={granted}")
        if lat == 0.0:
            ok &= at == "no_money" and granted == 1 and bal == 0
        else:
            ok &= granted == 2 and bal == -100
    print("C1 REPRODUCED (controls green, literal host takes the wrong branch)" if ok else "C1 NOT REPRODUCED / control failed")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()

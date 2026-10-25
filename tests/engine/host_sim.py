"""Reference host: the event processing contract (docs/host-contract.md) and its test.

A production setup in miniature:
* a "database" with atomic transactions: the user's snapshot, the processed
  update_ids, the queue of outgoing actions (outbox) with the event data and the
  host data (cart, address) are written together;
* a separate outbox executor (a background thread) runs the actions with
  idempotency keys; local calls (cart, address) run once per key;
* the "CRM" accepts a call once per key;
* the conditions are computed from real data: `cart_not_empty` from the cart,
  `in_delivery_zone` from the message text; before a step of a user the host
  waits until that user's unfinished actions have run (the barrier,
  docs/host-contract.md, section 1, step 3);
* a timer scheduler sends events with tokens;
* a user without a snapshot (new, or the snapshot was lost) is started with the
  update's event and the high-water mark of `seq`; a timer event of a lost
  snapshot is dropped (docs/host-contract.md, section 1, step 3; section 4).

Failures are injected at random: before the transaction commits, after the
commit before the execution, between the execution and the "done" mark. Repeated
and concurrent delivery of a user's events too. Users take different branches:
an empty cart at "Checkout", an address outside the zone.

    python3 host_sim.py ENGINE pizza.bot.json [--no-barrier]

`--no-barrier` — a host without the barrier (the conditions read the data before
the actions of the previous step have run): the test must be red.
"""

import copy
import json
import os
import random
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "clients", "python"))
from figram_client import Engine


class Crash(Exception):
    """An injected failure of the host process."""


LOCAL_CALLS = {"add_to_cart", "clear_cart", "save_size", "save_address"}


class DB:
    """Storage with atomic transactions (one lock per commit)."""

    def __init__(self):
        self._lock = threading.Lock()
        self.users = {}        # user -> {"snapshot": ..., "seen": set()}
        self.outbox = []       # {"key", "user", "action", "payload", "done"}
        self.data = {}         # user -> {"cart": int, "address": str | None}
        self.applied = set()   # keys of the local calls already run (a unique index)
        self.hwm = {}          # user -> the largest seq ever issued (docs/host-contract.md, section 4)

    def read(self, user):
        with self._lock:
            return copy.deepcopy(self.users.get(user, {"snapshot": None, "seen": set()}))

    def get_data(self, user):
        with self._lock:
            return dict(self.data.get(user, {"cart": 0, "address": None}))

    def commit(self, user, record, actions, payload):
        with self._lock:
            self.users[user] = record
            if record["snapshot"] is not None:
                self.hwm[user] = max(self.hwm.get(user, 0), record["snapshot"]["seq"])
            for i, a in enumerate(actions):
                self.outbox.append({"key": f"{user}:{record['snapshot']['seq']}:{i}",
                                    "user": user, "action": a, "payload": payload, "done": False})

    def apply_local(self, key, user, name, payload):
        """A local call in the same database: runs once per key."""
        with self._lock:
            if key in self.applied:
                return
            self.applied.add(key)
            d = self.data.setdefault(user, {"cart": 0, "address": None})
            if name == "add_to_cart":
                d["cart"] += 1
            elif name == "clear_cart":
                d["cart"] = 0
            elif name == "save_address":
                d["address"] = payload

    def pending(self, user=None):
        with self._lock:
            return [e for e in self.outbox if not e["done"] and (user is None or e["user"] == user)]

    def mark_done(self, entry):
        with self._lock:
            entry["done"] = True


class CRM:
    """An external system: a call with the same key runs once."""

    def __init__(self):
        self._lock = threading.Lock()
        self.executed = {}     # key -> (user, name, data)

    def call(self, key, user, name, data):
        with self._lock:
            self.executed.setdefault(key, (user, name, data))


class Host:
    def __init__(self, engine, crash_rate, naive=False, barrier=True, first_event=True, hwm=True):
        self.naive = naive         # control host without the contract
        self.barrier = barrier     # host contract, section 1, step 3: earlier actions run before the conditions
        self.first_event = first_event  # section 1, step 3: a new user is started with the update's event
        self.hwm = hwm             # section 4: a user without a snapshot continues the high-water mark
        self.crashes = 0
        self.engine = engine
        self.db = DB()
        self.crm = CRM()
        self.timers = {}       # (user, timer) -> token: the "scheduler"
        self.sent = []         # sent messages
        self.crash_rate = crash_rate
        self._user_locks = {}
        self._locks_lock = threading.Lock()
        self._exec_lock = threading.Lock()
        self.order_log = {}    # user -> (event, token, conditions) in processing order
        self._stop = threading.Event()
        self._executor = None

    def _maybe_crash(self, where):
        if random.random() < self.crash_rate:
            self.crashes += 1
            raise Crash(where)

    def _user_lock(self, user):
        with self._locks_lock:
            return self._user_locks.setdefault(user, threading.Lock())

    def guards(self, user, payload, used):
        """Conditions from the host data (cart) and from the event data (address text)."""
        def f(names):
            d = self.db.get_data(user)
            vals = {"cart_not_empty": d["cart"] > 0,
                    "in_delivery_zone": bool(payload) and payload.startswith("zone:")}
            out = {n: vals.get(n, False) for n in names}
            used.update(out)
            return out
        return f

    def handle(self, user, update_id, event, token=None, payload=None):
        """Process one update. Returns the status; Crash — a failure."""
        with self._user_lock(user):                     # 1. one at a time per user
            if self.barrier:
                self.drain(user)                         # barrier: earlier actions have run
            rec = self.db.read(user)
            if update_id in rec["seen"] and not self.naive:  # 2. repeated delivery
                return "duplicate"
            used = {}
            start = None
            if rec["snapshot"] is None and token is not None and self.first_event:
                # a new user has no timers: the event belongs to a lost snapshot (section 1, step 3)
                rec["seen"].add(update_id)
                self.db.commit(user, rec, [], payload)
                return "dropped"
            if rec["snapshot"] is None:
                start = (self.db.hwm.get(user) if self.hwm else None,)
                if self.first_event:
                    r = self.engine.start(user, start[0], event, self.guards(user, payload, used))
                else:
                    r = self.engine.start(user, start[0])
                    event = None
                for k in [k for k, t in self.timers.items() if k[0] == user and start[0] is not None and t <= start[0]]:
                    self.timers.pop(k, None)             # section 4: timers of the lost snapshot
            else:
                r = self.engine.step(user, rec["snapshot"], event, self.guards(user, payload, used), token)
            self._maybe_crash("before commit")           # failure: nothing written
            rec["snapshot"] = r["snapshot"]
            rec["seen"].add(update_id)
            self.db.commit(user, rec, r["actions"], payload)  # 3. atomically: snapshot + outbox
            self.order_log.setdefault(user, []).append((event, token, used, start))
        self._maybe_crash("after commit")                 # failure: the actions are in the outbox
        return "ok"

    def drain(self, user=None):
        """The outbox executor: every action runs with an idempotency key."""
        with self._exec_lock:
            for e in self.db.pending(user):
                a = e["action"]
                if "call" in a:
                    key = e["key"] if not self.naive else f"{e['key']}#{random.random()}"
                    if a["call"] in LOCAL_CALLS:
                        self.db.apply_local(key, e["user"], a["call"], e["payload"])
                    else:
                        self.crm.call(key, e["user"], a["call"], self.db.get_data(e["user"]))
                elif "send" in a:
                    self.sent.append((e["key"], e["user"], a["send"]))
                elif "start_timer" in a:
                    self.timers[(e["user"], a["start_timer"])] = a["token"]
                elif "cancel_timer" in a:
                    self.timers.pop((e["user"], a["cancel_timer"]), None)
                self._maybe_crash("before the done mark")    # failure: done, not marked
                self.db.mark_done(e)

    def start_executor(self, poll=0.05):
        """A separate executor (docs/host-contract.md, section 2): a background thread
        that polls the outbox every `poll` seconds (like a typical outbox poller);
        a failure = a restart."""
        def loop():
            while not self._stop.is_set():
                try:
                    self.drain()
                except Crash:
                    pass
                time.sleep(poll)
        self._executor = threading.Thread(target=loop, daemon=True)
        self._executor.start()

    def stop_executor(self):
        self._stop.set()
        if self._executor:
            self._executor.join()


def deliver(host, user, update_id, event, token=None, payload=None, attempts=50):
    """Delivery "like Telegram": repeat until the host acknowledges."""
    for _ in range(attempts):
        try:
            return host.handle(user, update_id, event, token, payload)
        except Crash:
            time.sleep(0.001)
    raise RuntimeError("delivery failed")


ORDER = ["cmd:start", "btn:order", "btn:pizza", "btn:size", "btn:add",
         "btn:checkout", "msg:text", "payment:success"]
# "Checkout" with an empty cart first (the false branch → catalog), then the order.
EMPTY_FIRST = ["cmd:start", "btn:order", "btn:cart", "btn:checkout", "btn:pizza", "btn:size",
               "btn:add", "btn:checkout", "msg:text", "payment:success"]


def plan(u):
    """The events of user u and the expected outcome: an address in the zone → done
    and one order, outside the zone → pickup and none."""
    events = EMPTY_FIRST if u % 3 == 0 else ORDER
    in_zone = u % 2 == 0
    text = "zone:1 Main St" if in_zone else "far:99 Country Rd"
    seq = [(ev, text if ev == "msg:text" else None) for ev in events]
    return seq, ("done", 1) if in_zone else ("pickup", 0)


def scenario_orders(engine_path, doc, users=30, crash_rate=0.15, naive=False, barrier=True):
    random.seed(1)
    engine = Engine(engine_path, doc, processes=2)
    host = Host(engine, crash_rate, naive, barrier)
    host.start_executor()

    def user_stream(u):
        for i, (ev, payload) in enumerate(plan(u)[0]):
            with ThreadPoolExecutor(2) as pool:           # a concurrent duplicate
                list(pool.map(lambda _: deliver(host, f"u{u}", i, ev, payload=payload), range(2)))

    with ThreadPoolExecutor(16) as pool:
        list(pool.map(user_stream, range(users)))
    host.stop_executor()
    for _ in range(100):                                  # finish the outbox after failures
        try:
            host.drain()
            break
        except Crash:
            pass
    host.crash_rate = 0
    host.drain()

    orders = [v for v in host.crm.executed.values() if v[1] == "create_order"]
    expected_ok = 0
    wrong = []
    for u in range(users):
        exp_at, exp_orders = plan(u)[1]
        text = next(p for ev, p in plan(u)[0] if ev == "msg:text")
        at = host.db.users[f"u{u}"]["snapshot"]["at"]
        mine = [o for o in orders if o[0] == f"u{u}"]
        # An order has a non-empty cart and the address from the user's message.
        good = all(o[2]["cart"] > 0 and o[2]["address"] == text for o in mine)
        if at == exp_at and len(mine) == exp_orders and good:
            expected_ok += 1
        else:
            wrong.append((f"u{u}", at, len(mine), [(o[2]["cart"], o[2]["address"]) for o in mine]))
    pending = len(host.db.pending())
    total = sum(len(plan(u)[0]) for u in range(users))
    # Order: the snapshot of every user equals a sequential replay of their events in
    # processing order with the same conditions, so no update is lost.
    lost = 0
    for u in range(users):
        snap = None
        for ev, tok, used, start in host.order_log[f"u{u}"]:
            g = (lambda ns, used=used: {n: used[n] for n in ns})
            snap = (engine.start("ref", start[0], ev, g if ev else None) if start
                    else engine.step("ref", snap, ev, g, tok))["snapshot"]
        if snap != host.db.users[f"u{u}"]["snapshot"]:
            lost += 1
    engine.close()
    name = "NAIVE host" if naive else ("host by the contract" if barrier else "host WITHOUT the barrier")
    print(f"{name}, failures injected {host.crashes}")
    print(f"  users {users}: outcome as expected (done/pickup, orders 1/0, cart and address in the order) for {expected_ok}, "
          f"orders in the CRM {len(orders)}, events processed {sum(len(v) for v in host.order_log.values())} "
          f"of {total} (repeated deliveries), lost updates {lost}, actions not run {pending}")
    if wrong:
        print(f"  not as expected: {wrong[:5]}{' …' if len(wrong) > 5 else ''}")
    return expected_ok == users and lost == 0 and pending == 0


def scenario_stale_timer(engine_path, doc):
    engine = Engine(engine_path, doc)
    host = Host(engine, 0)

    def step(i, ev, token=None):
        deliver(host, u, i, ev, token)
        host.drain()

    u = "t"
    step(0, "cmd:start")
    for i, ev in enumerate(["btn:order", "btn:cart"], 1):  # the cart starts the reminder
        step(i, ev)
    old = host.timers[(u, "cart_reminder")]
    step(3, "btn:cancel")                                   # leaving cancels the timer
    step(4, "btn:order")
    step(5, "btn:cart")                                     # a new run, a new token
    new = host.timers[(u, "cart_reminder")]
    before = host.db.users[u]["snapshot"]["at"]
    step(6, "timer:cart_reminder", token=old)               # a late old event
    after_old = host.db.users[u]["snapshot"]["at"]
    step(7, "timer:cart_reminder", token=new)
    after_new = host.db.users[u]["snapshot"]["at"]
    engine.close()
    print(f"timer: tokens {old} → {new}; old event: {before} → {after_old}; "
          f"current: → {after_new}")
    return old != new and after_old == before == "cart" and after_new == "reminder"


SECOND = ["btn:order", "btn:pizza", "btn:size", "btn:add", "btn:checkout", "msg:text", "payment:success"]


def plan_lost(u):
    """A user whose snapshot is lost (the row only: update marks, data and outbox survive).
    Even users: an order, the loss, then a button of an old menu message (`btn:order`) and a
    second order to another address. Odd users: the loss at the cart while the reminder runs,
    the old timer fires while there is no snapshot, then `btn:order` from an old message and
    the order. The cart is never cleared, so every order after the loss has 2 pizzas."""
    a1, a2 = "zone:1 Main St", "zone:2 Second Ave"
    first = ORDER if u % 2 == 0 else ORDER[:5]
    events = [(ev, a1 if ev == "msg:text" else None) for ev in first]
    events += [("LOSE", None)] + ([] if u % 2 == 0 else [("TIMER-OLD", None)])
    events += [(ev, a2 if ev == "msg:text" else None) for ev in SECOND]
    return events, ([(1, a1)] if u % 2 == 0 else []) + [(2, a2)]


def scenario_lost_snapshot(engine_path, doc, users=12, crash_rate=0.15, **host_args):
    """A new user with an update (docs/host-contract.md, section 1, step 3; section 4): the host
    starts a user without a snapshot with the update's event and the high-water mark of `seq`,
    and drops a timer event of the lost snapshot."""
    random.seed(2)
    engine = Engine(engine_path, doc, processes=2)
    host = Host(engine, crash_rate, **host_args)
    host.start_executor()
    old_token = {}

    def user_stream(u):
        user = f"l{u}"
        for i, (ev, payload) in enumerate(plan_lost(u)[0]):
            token = None
            if ev == "LOSE":
                with host._user_lock(user):
                    host.drain(user)
                    old_token[user] = host.timers.get((user, "cart_reminder"))
                    with host.db._lock:
                        host.db.users[user]["snapshot"] = None
                continue
            if ev == "TIMER-OLD":
                ev, token = "timer:cart_reminder", old_token[user]
            uid = f"timer:cart_reminder:{token}" if token is not None else i
            with ThreadPoolExecutor(2) as pool:
                list(pool.map(lambda _: deliver(host, user, uid, ev, token, payload), range(2)))

    with ThreadPoolExecutor(8) as pool:
        list(pool.map(user_stream, range(users)))
    host.stop_executor()
    for _ in range(100):
        try:
            host.drain()
            break
        except Crash:
            pass
    host.crash_rate = 0
    host.drain()
    orders = [v for v in host.crm.executed.values() if v[1] == "create_order"]
    wrong = []
    for u in range(users):
        user = f"l{u}"
        got = sorted((o[2]["cart"], o[2]["address"]) for o in orders if o[0] == user)
        snap = host.db.users[user]["snapshot"]
        if snap is None or snap["at"] != "done" or got != sorted(plan_lost(u)[1]):
            wrong.append((user, snap and snap["at"], got))
    pending = len(host.db.pending())
    engine.close()
    print(f"lost snapshot ({host_args or 'host by the contract'}), failures injected {host.crashes}: "
          f"users {users}, not as expected {len(wrong)}, actions not run {pending}")
    if wrong:
        print(f"  not as expected: {wrong[:4]}{' …' if len(wrong) > 4 else ''}")
    return not wrong and pending == 0


if __name__ == "__main__":
    engine_path, doc = sys.argv[1], sys.argv[2]
    barrier = "--no-barrier" not in sys.argv[3:]
    ok1 = scenario_orders(engine_path, doc, barrier=barrier)
    ok2 = scenario_stale_timer(engine_path, doc)
    print("--- control: a host without duplicate filtering and without idempotency keys")
    # The control is probabilistic (a duplicate order happens only with a failure at the right
    # place; one attempt catches it about 85 % of the time): up to eight attempts, one red one is
    # enough, so a correct test fails by chance about once in 10^7 runs. The main scenario is not
    # repeated.
    for attempt in range(8):
        control = scenario_orders(engine_path, doc, naive=True)
        if not control:
            break
    print("  the test catches the violation:", "yes" if not control else "FAIL", f"(control attempts: {attempt + 1})")
    ok2 = ok2 and not control
    ok3 = scenario_lost_snapshot(engine_path, doc)
    print("--- controls: a host that starts a user without a snapshot without the update's event, "
          "or without the high-water mark")
    lost_controls = [scenario_lost_snapshot(engine_path, doc, first_event=False),
                     scenario_lost_snapshot(engine_path, doc, hwm=False)]
    print("  the test catches the violations:", "yes" if not any(lost_controls) else "FAIL")
    ok2 = ok2 and ok3 and not any(lost_controls)
    print("contract holds" if ok1 and ok2 else "CONTRACT VIOLATED")
    sys.exit(0 if ok1 and ok2 else 1)

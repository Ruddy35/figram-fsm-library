"""Reference host: the event processing contract (docs/host-contract.md) and its test.

A production setup in miniature:
* a "database" with atomic transactions: the user's snapshot, the processed
  update_ids, the queue of outgoing actions (outbox) with the event data, the
  host data (cart, address) and the inbound queue of outcome events (inbox) are
  written together;
* a separate outbox executor (a background thread) runs the sequential actions
  in order with idempotency keys; local calls (cart, address) run once per key;
* background actions (`"detached": true`: `notify_kitchen`) run from a separate
  queue by a background executor, outside the per-user barrier; after a few
  failed attempts such an action goes to the failure queue with an alert and the
  user goes on (docs/host-contract.md, section 2, "Background actions");
* the "CRM" accepts a call once per key and answers it: `create_order` has the
  outcomes created/rejected. The executor performs it and records exactly one
  outcome event `call:create_order:<outcome>` with the token of the call in the
  same transaction that marks the action executed: `rejected` with the reason
  when the CRM says no (a business answer is an outcome), `failed` when the CRM
  does not answer after a few attempts and has not acted (section 2, "Outcomes": when the
  answer was lost after the CRM acted, the host reconciles by the key of the call and delivers
  the real outcome). The outcome is an
  event of the user and goes through section 1 like an update (the update_id
  `call:create_order:<token>`); its details (the reason) are stored with it and
  the condition `address_rejected` reads them;
* the conditions are computed from real data: `cart_not_empty` from the cart,
  `in_delivery_zone` from the message text; before a step of a user the host
  waits until that user's unfinished sequential actions have run (the barrier,
  docs/host-contract.md, section 1, step 3);
* a timer scheduler sends events with tokens;
* a user without a snapshot (new, or the snapshot was lost) is started with the
  update's event and the high-water mark of `seq`; a timer or outcome event of a
  lost snapshot is dropped (docs/host-contract.md, section 1, step 3; section 4).

The users are scripted by what they see: each sends the next event for the message
of its committed snapshot and waits while the snapshot waits for an outcome.
Failures are injected at random: before the transaction commits, after the commit
before the execution, between the execution and the "done" mark, between the
CRM's answer and the transaction that records it, after an outcome is processed
before it leaves the inbox. Repeated and concurrent delivery of a user's events
too. Users take different branches: an empty cart at "Checkout", an address
outside the zone, an address the CRM rejects, a CRM that rejects the order for
another reason, a CRM that does not answer.

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


class Down(Exception):
    """An external system did not answer (a timeout, a server error)."""


LOCAL_CALLS = {"add_to_cart", "clear_cart", "save_size", "save_address"}
MAX_ATTEMPTS = 3          # attempts of an external call before it counts as failed
GOOD = "zone:1 Main St"   # an address in the zone that the CRM accepts
CLOSED = "zone:13 Closed St"   # in the zone, but the CRM rejects it (`address_rejected`)
FAR = "far:99 Country Rd"      # outside the delivery zone


class DB:
    """Storage with atomic transactions (one lock per commit)."""

    def __init__(self):
        self._lock = threading.Lock()
        self.users = {}        # user -> {"snapshot": ..., "seen": set()}
        self.outbox = []       # {"key", "user", "action", "payload", "done", "detached", "attempts", "failed"}
        self.inbox = []        # outcome events not yet processed: {"user", "update_id", "event", "token", "details"}
        self.inbox_ids = set()  # (user, update_id) of every outcome event ever recorded
        self.failures = []     # the failure queue (entries of the outbox), each with an alert
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
                self.outbox.append({"key": f"{user}:{record['snapshot']['seq']}:{i}", "user": user, "action": a,
                                    "payload": payload, "done": False, "detached": a.get("detached") is True,
                                    "attempts": 0, "failed": False})

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

    def pending(self, user=None, detached=None):
        """Actions not executed and not in the failure queue, in commit order."""
        with self._lock:
            return [e for e in self.outbox if not e["done"] and not e["failed"]
                    and (user is None or e["user"] == user) and (detached is None or e["detached"] == detached)]

    def blocked(self, user):
        """The user's queue waits for a decision: a sequential action is in the failure queue."""
        with self._lock:
            return any(e["user"] == user and e["failed"] and e["blocking"] for e in self.failures)

    def attempt(self, entry):
        with self._lock:
            entry["attempts"] += 1
            return entry["attempts"]

    def mark_done(self, entry):
        with self._lock:
            entry["done"] = True

    def record_outcome(self, entry, event):
        """One transaction: the action is executed and its outcome event is recorded."""
        with self._lock:
            entry["done"] = True
            self._add_inbox(event)

    def add_inbox(self, event):
        with self._lock:
            self._add_inbox(event)

    def _add_inbox(self, event):
        if (event["user"], event["update_id"]) not in self.inbox_ids:
            self.inbox_ids.add((event["user"], event["update_id"]))
            self.inbox.append(event)

    def inbox_rows(self, user=None):
        with self._lock:
            return [r for r in self.inbox if user is None or r["user"] == user]

    def remove_inbox(self, row):
        with self._lock:
            if row in self.inbox:
                self.inbox.remove(row)

    def fail(self, entry, reason, blocking):
        """The failure queue with an alert; a sequential action blocks the user's queue."""
        with self._lock:
            entry["failed"] = True
            entry["blocking"] = blocking
            entry["alert"] = f"{entry['key']} {entry['action']['call']}: {reason}"
            self.failures.append(entry)


class CRM:
    """An external system: a call with the same key runs once and gets the same answer.
    `down[user]` — how many calls of the user it does not answer; `lost[user]` — how many answers
    to the user's calls are lost after the CRM acted (the order is created, the host hears
    nothing); `stock_out` — users whose next order it rejects for another reason than the
    address. `lookup` is the reconciliation query: the answer to a key, if the CRM acted."""

    def __init__(self, down=None, stock_out=(), lost=None):
        self._lock = threading.Lock()
        self.executed = {}     # key -> (user, name, data, (outcome, details))
        self.down = dict(down or {})
        self.lost = dict(lost or {})
        self.stock_out = set(stock_out)

    def lookup(self, key):
        with self._lock:
            return self.executed[key][3] if key in self.executed else None

    def _lose(self, user):
        if self.lost.get(user, 0) > 0:
            self.lost[user] -= 1
            raise Down("the answer of the CRM is lost")

    def call(self, key, user, name, data):
        with self._lock:
            if key in self.executed:
                self._lose(user)
                return self.executed[key][3]
            if self.down.get(user, 0) > 0:
                self.down[user] -= 1
                raise Down("the CRM does not answer")
            answer = ("done", {})
            if name == "create_order":
                if data["address"] == CLOSED:
                    answer = ("rejected", {"reason": "address"})
                elif user in self.stock_out:
                    self.stock_out.discard(user)
                    answer = ("rejected", {"reason": "out of dough"})
                else:
                    answer = ("created", {"order": key})
            self.executed[key] = (user, name, data, answer)
            self._lose(user)
            return answer

    def orders(self, user=None):
        return [v for v in self.executed.values()
                if v[1] == "create_order" and v[3][0] == "created" and (user is None or v[0] == user)]


class Kitchen:
    """The system behind the background action `notify_kitchen`: once per key. `broken` —
    users for whom it never answers; `flaky` — the first attempt of every key fails."""

    def __init__(self, broken=(), flaky=False):
        self._lock = threading.Lock()
        self.executed = {}
        self.broken = set(broken)
        self.flaky = flaky
        self._tried = set()

    def call(self, key, user):
        with self._lock:
            if user in self.broken:
                raise Down("the kitchen does not answer")
            if self.flaky and key not in self._tried:
                self._tried.add(key)
                raise Down("the kitchen is busy")
            self.executed.setdefault(key, user)
            return True


class Host:
    def __init__(self, engine, crash_rate, naive=False, barrier=True, first_event=True, hwm=True,
                 atomic_outcome=True, background_queue=True, crm=None, kitchen=None, reconcile=True):
        self.naive = naive         # control host without the contract
        self.barrier = barrier     # host contract, section 1, step 3: earlier actions run before the conditions
        self.first_event = first_event  # section 1, step 3: a new user is started with the update's event
        self.hwm = hwm             # section 4: a user without a snapshot continues the high-water mark
        self.atomic_outcome = atomic_outcome  # section 2: the outcome is recorded with the "executed" mark
        self.background_queue = background_queue  # section 2: background actions outside the user's queue
        self.reconcile = reconcile  # section 2: before `failed`, ask the system whether the call took effect
        self.crashes = 0
        self.engine = engine
        self.db = DB()
        self.crm = crm or CRM()
        self.kitchen = kitchen or Kitchen()
        self.timers = {}       # (user, timer) -> token: the "scheduler"
        self.sent = []         # sent messages
        self.crash_rate = crash_rate
        self._user_locks = {}
        self._locks_lock = threading.Lock()
        self._exec_lock = threading.Lock()   # the sequential executor
        self._bg_lock = threading.Lock()     # the background executor
        self.order_log = {}    # user -> (event, token, conditions) in processing order
        self.dropped = []      # (user, event) dropped without a snapshot
        self._stop = threading.Event()
        self._threads = []

    def _maybe_crash(self, where):
        if random.random() < self.crash_rate:
            self.crashes += 1
            raise Crash(where)

    def _user_lock(self, user):
        with self._locks_lock:
            return self._user_locks.setdefault(user, threading.Lock())

    def guards(self, user, payload, used):
        """Conditions from the host data (cart), from the event data (address text) and from
        the details of an outcome (the reason the CRM gave)."""
        def f(names):
            d = self.db.get_data(user)
            vals = {"cart_not_empty": d["cart"] > 0,
                    "in_delivery_zone": isinstance(payload, str) and payload.startswith("zone:"),
                    "address_rejected": isinstance(payload, dict) and payload.get("reason") == "address"}
            out = {n: vals.get(n, False) for n in names}
            used.update(out)
            return out
        return f

    def handle(self, user, update_id, event, token=None, payload=None):
        """Process one update. Returns the status; Crash — a failure."""
        with self._user_lock(user):                     # 1. one at a time per user
            if self.barrier:
                self.drain(user)                         # barrier: earlier sequential actions have run
                if self.db.blocked(user):
                    return "blocked"                     # the user's queue waits for a decision
            rec = self.db.read(user)
            if update_id in rec["seen"] and not self.naive:  # 2. repeated delivery
                return "duplicate"
            used = {}
            start = None
            if rec["snapshot"] is None and token is not None and self.first_event:
                # a new user has no timers and no waits: the event belongs to a lost snapshot
                rec["seen"].add(update_id)
                self.db.commit(user, rec, [], payload)
                self.dropped.append((user, event))
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
            elif event.startswith("call:"):
                action, _, outcome = event[len("call:"):].rpartition(":")
                r = self.engine.outcome(user, rec["snapshot"], action, outcome, token, self.guards(user, payload, used))
            else:
                r = self.engine.step(user, rec["snapshot"], event, self.guards(user, payload, used), token)
            self._maybe_crash("before commit")           # failure: nothing written
            rec["snapshot"] = r["snapshot"]
            rec["seen"].add(update_id)
            self.db.commit(user, rec, r["actions"], payload)  # 3. atomically: snapshot + outbox
            self.order_log.setdefault(user, []).append((event, token, used, start))
        self._maybe_crash("after commit")                 # failure: the actions are in the outbox
        return "ok"

    def _key(self, e):
        return e["key"] if not self.naive else f"{e['key']}#{random.random()}"

    def _external(self, e, call):
        """An external call with up to MAX_ATTEMPTS attempts (the count survives failures of
        the host); returns the answer or None when the attempts are exhausted."""
        while e["attempts"] < MAX_ATTEMPTS:
            try:
                return call()
            except Down:
                self.db.attempt(e)
        return None

    def _run(self, e):
        """Execute one outbox action."""
        a = e["action"]
        if "call" in a:
            key, name = self._key(e), a["call"]
            if name in LOCAL_CALLS:
                self.db.apply_local(key, e["user"], name, e["payload"])
            elif name == "notify_kitchen":
                if self._external(e, lambda: self.kitchen.call(key, e["user"])) is None:
                    # a background action goes to the failure queue and the user goes on;
                    # a sequential one blocks the user's queue until a decision
                    self.db.fail(e, "the kitchen does not answer", blocking=not e["detached"] or not self.background_queue)
                    return
            else:
                answer = self._external(e, lambda: self.crm.call(key, e["user"], name, self.db.get_data(e["user"])))
                if answer is None and "token" in a and self.reconcile:
                    # no answer is not "not performed": reconcile by the key of the call
                    answer = self.crm.lookup(key)
                if "token" in a:
                    # an action with outcomes: exactly one outcome event with the token of the call
                    outcome, details = answer if answer is not None else ("failed", {"error": "no answer"})
                    event = {"user": e["user"], "update_id": f"call:{name}:{a['token']}",
                             "event": f"call:{name}:{outcome}", "token": a["token"], "details": details}
                    self._maybe_crash("after the answer, before it is recorded")  # the call runs again
                    if self.atomic_outcome:
                        self.db.record_outcome(e, event)          # one transaction
                    else:
                        self.db.mark_done(e)                      # the control: two steps
                        self._maybe_crash("after the done mark, before the outcome is recorded")
                        self.db.add_inbox(event)
                    return
                if answer is None:
                    self.db.fail(e, "the CRM does not answer", blocking=True)
                    return
        elif "send" in a:
            self.sent.append((e["key"], e["user"], a["send"]))
        elif "start_timer" in a:
            self.timers[(e["user"], a["start_timer"])] = a["token"]
        elif "cancel_timer" in a:
            self.timers.pop((e["user"], a["cancel_timer"]), None)
        self._maybe_crash("before the done mark")    # failure: done, not marked
        self.db.mark_done(e)

    def drain(self, user=None):
        """The outbox executor for the sequential actions (with the background actions too, if
        the host has no separate queue for them); a user's actions stop at a blocking failure."""
        with self._exec_lock:
            for e in self.db.pending(user, None if not self.background_queue else False):
                if not self.db.blocked(e["user"]):
                    self._run(e)

    def drain_background(self):
        """The background executor: the background actions, outside the users' queues."""
        with self._bg_lock:
            for e in self.db.pending(None, True):
                self._run(e)

    def deliver_outcomes(self):
        """The outcome events go through section 1 like updates: delivered twice and
        concurrently, removed from the inbox after processing."""
        for row in self.db.inbox_rows():
            with ThreadPoolExecutor(2) as pool:
                statuses = list(pool.map(lambda _: deliver(self, row["user"], row["update_id"], row["event"],
                                                           row["token"], row["details"]), range(2)))
            if "blocked" in statuses:
                continue                                 # stays in the inbox until a decision
            self._maybe_crash("after the outcome is processed, before it leaves the inbox")
            self.db.remove_inbox(row)

    def start_executor(self, poll=0.02):
        """Separate executors (docs/host-contract.md, section 2): background threads that poll
        the outbox and the inbox (like a typical outbox poller); a failure = a restart."""
        def loop(work):
            while not self._stop.is_set():
                try:
                    work()
                except Crash:
                    pass
                time.sleep(poll)
        for work in ([self.drain, self.deliver_outcomes]
                     + ([self.drain_background] if self.background_queue else [])):
            t = threading.Thread(target=loop, args=(work,), daemon=True)
            t.start()
            self._threads.append(t)

    def stop_executor(self):
        self._stop.set()
        for t in self._threads:
            t.join()

    def finish(self):
        """After the run: execute what is left, without failures."""
        self.stop_executor()
        self.crash_rate = 0
        for _ in range(20):
            self.drain()
            if self.background_queue:
                self.drain_background()
            self.deliver_outcomes()
            if not self.db.pending() and not self.db.inbox_rows():
                break

    def stuck(self, user):
        """The user's snapshot waits for an outcome that nothing will bring: no action of the
        user is waiting to run and no outcome event of the user is in the inbox."""
        with self._exec_lock, self._bg_lock:
            snap = self.db.read(user)["snapshot"]
            return bool(snap and snap.get("pending")) and not self.db.pending(user) \
                and not self.db.inbox_rows(user)


def deliver(host, user, update_id, event, token=None, payload=None, attempts=50):
    """Delivery "like Telegram": repeat until the host acknowledges. A blocked queue
    stays blocked: after a few attempts the update is left unprocessed."""
    blocked = 0
    for _ in range(attempts):
        try:
            status = host.handle(user, update_id, event, token, payload)
            if status != "blocked":
                return status
            blocked += 1
            if blocked >= 5:
                return status
            time.sleep(0.01)
        except Crash:
            time.sleep(0.001)
    raise RuntimeError("delivery failed")


def settle(host, user, limit=60.0):
    """The user waits while the snapshot waits for an outcome; False — the outcome is lost."""
    deadline = time.monotonic() + limit
    while time.monotonic() < deadline:
        snap = host.db.read(user)["snapshot"]
        if not (snap and snap.get("pending")):
            return True
        if host.stuck(user):
            return False
        time.sleep(0.005)
    return False


def run_user(host, user, policy, sent, max_steps=60):
    """A user who sends the next event for the message of the committed snapshot; every
    update is delivered twice and concurrently. Returns how the run ended."""
    for i in range(max_steps):
        if not settle(host, user):
            return "stuck"
        snap = host.db.read(user)["snapshot"]
        nxt = policy(snap["at"] if snap else None)
        if nxt is None:
            return "finished"
        ev, payload = nxt
        sent.add((user, i))
        with ThreadPoolExecutor(2) as pool:           # a concurrent duplicate
            statuses = list(pool.map(lambda _: deliver(host, user, i, ev, payload=payload), range(2)))
        if "blocked" in statuses:
            return "blocked"
    return "too many steps"


def order_policy(addresses, empty_first=False, after=None):
    """What a user does on each message: order a pizza, type the addresses in turn, retry a
    failed order; `empty_first` — press "Checkout" with an empty cart first; `after` — the
    events after the order (from `done`, then each message once)."""
    st = {"empty": empty_first, "addresses": list(addresses), "after": list(after or []), "done": False}

    def policy(at):
        if st["done"]:
            return st["after"].pop(0) if st["after"] else None
        if at in (None, "menu"):
            return ("cmd:start", None) if at is None else ("btn:order", None)
        if at == "catalog":
            if st["empty"]:
                st["empty"] = False
                return ("btn:cart", None)
            return ("btn:pizza", None)
        step = {"cart": "btn:checkout", "pick_size": "btn:size", "pick_toppings": "btn:add",
                "pay": "payment:success", "order_failed": "btn:retry"}
        if at in step:
            return (step[at], None)
        if at == "address":
            return ("msg:text", st["addresses"].pop(0))
        if at in ("done", "pickup"):
            st["done"] = True
            return policy(at)
        raise RuntimeError(f"the user does not expect the message {at}")
    return policy


def plan(u):
    """The addresses of user u, whether the CRM rejects the order for another reason or does
    not answer, and the expected outcome: an address in the zone → done and one order, outside
    the zone → pickup and none."""
    in_zone = u % 2 == 0
    closed = in_zone and u % 5 == 2           # the CRM rejects the address, the user types another
    down = in_zone and u % 5 == 4             # the CRM does not answer: failed, the user retries
    stock = in_zone and u % 7 == 0 and not closed and not down   # rejected for another reason, retry
    lost = in_zone and u % 11 in (6, 8) and not closed and not down and not stock  # created, answer lost
    addresses = [CLOSED, GOOD] if closed else [GOOD if in_zone else FAR]
    return {"addresses": addresses, "empty_first": u % 3 == 0, "down": down, "stock": stock, "lost": lost,
            "expect": ("done", 1, addresses[-1]) if in_zone else ("pickup", 0, None)}


def outcome_report(host, users):
    """Exactly one outcome event per call with a token, processed once; no waits left."""
    calls = {(e["user"], e["action"]["token"]) for e in host.db.outbox if "token" in e["action"] and "call" in e["action"]}
    got = [(u, tok, ev) for u in users for ev, tok, _, _ in host.order_log.get(u, []) if ev and ev.startswith("call:")]
    seen = [(u, tok) for u, tok, _ in got]
    lost = len(calls - set(seen))
    twice = len(seen) - len(set(seen))
    foreign = len(set(seen) - calls)
    waits = sum(1 for u in users if host.db.users.get(u, {}).get("snapshot") and host.db.users[u]["snapshot"].get("pending"))
    kinds = {k: sum(1 for _, _, ev in got if ev.endswith(":" + k)) for k in ("created", "rejected", "failed")}
    return calls, lost, twice, foreign, waits, kinds


def replay_lost(host, engine, users):
    """The snapshot of every user equals a sequential replay of their events in processing
    order with the same conditions, so no update is lost."""
    lost = 0
    for u in users:
        snap = None
        for ev, tok, used, start in host.order_log.get(u, []):
            g = (lambda ns, used=used: {n: used[n] for n in ns})
            snap = (engine.start("ref", start[0], ev, g if ev else None) if start
                    else engine.step("ref", snap, ev, g, tok))["snapshot"]
        if snap != host.db.users[u]["snapshot"]:
            lost += 1
    return lost


def scenario_orders(engine_path, doc, users=30, crash_rate=0.15, naive=False, barrier=True, atomic_outcome=True,
                    reconcile=True):
    random.seed(1)
    engine = Engine(engine_path, doc, processes=2)
    plans = {f"u{u}": plan(u) for u in range(users)}
    crm = CRM(down={k: MAX_ATTEMPTS for k, p in plans.items() if p["down"]},
              stock_out={k for k, p in plans.items() if p["stock"]},
              lost={k: MAX_ATTEMPTS for k, p in plans.items() if p["lost"]})
    host = Host(engine, crash_rate, naive, barrier, atomic_outcome=atomic_outcome, crm=crm,
                kitchen=Kitchen(flaky=True), reconcile=reconcile)
    host.start_executor()
    sent = set()
    ends = {}

    def user_stream(user):
        p = plans[user]
        ends[user] = run_user(host, user, order_policy(p["addresses"], p["empty_first"]), sent)

    with ThreadPoolExecutor(16) as pool:
        list(pool.map(user_stream, plans))
    host.finish()

    expected_ok = 0
    wrong = []
    for user, p in plans.items():
        exp_at, exp_orders, address = p["expect"]
        at = host.db.users[user]["snapshot"]["at"]
        mine = crm.orders(user)
        # An order has a non-empty cart and the address the user typed last.
        good = all(o[2]["cart"] > 0 and o[2]["address"] == address for o in mine)
        if at == exp_at and len(mine) == exp_orders and good and ends[user] == "finished":
            expected_ok += 1
        else:
            wrong.append((user, ends[user], at, len(mine), [(o[2]["cart"], o[2]["address"]) for o in mine]))
    pending = len(host.db.pending()) + len(host.db.inbox_rows())
    calls, lost_outcomes, twice, foreign, waits, kinds = outcome_report(host, plans)
    total = len(sent) + len(host.db.inbox_ids)
    lost = replay_lost(host, engine, plans)
    notify = [e for e in host.db.outbox if e["action"].get("call") == "notify_kitchen"]
    kitchen_ok = len(host.kitchen.executed) == len(notify) == len(crm.orders()) and not host.db.failures
    engine.close()
    name = ("NAIVE host" if naive else "host that delivers `failed` without reconciling" if not reconcile
            else "host by the contract" if barrier and atomic_outcome
            else "host WITHOUT the barrier" if not barrier else "host that records the outcome separately")
    print(f"{name}, failures injected {host.crashes}")
    print(f"  users {users}: outcome as expected (done/pickup, orders 1/0, cart and address in the order) for {expected_ok}, "
          f"orders in the CRM {len(crm.orders())}, events processed {sum(len(v) for v in host.order_log.values())} "
          f"of {total} (repeated deliveries), lost updates {lost}, actions not run {pending}")
    print(f"  outcomes: calls with a token {len(calls)}, outcome events lost {lost_outcomes}, processed twice {twice}, "
          f"without a call {foreign}, waits left {waits} (created {kinds['created']}, rejected {kinds['rejected']}, "
          f"failed {kinds['failed']}); background actions run {len(host.kitchen.executed)} of {len(notify)}, "
          f"in the failure queue {len(host.db.failures)}")
    if wrong:
        print(f"  not as expected: {wrong[:5]}{' …' if len(wrong) > 5 else ''}")
    outcomes_ok = (calls and lost_outcomes == 0 and twice == 0 and foreign == 0 and waits == 0
                   and kinds["rejected"] > 0 and kinds["failed"] > 0)
    return expected_ok == users and lost == 0 and pending == 0 and outcomes_ok and kitchen_ok


def scenario_background(engine_path, doc, users=12, crash_rate=0.1, background_queue=True):
    """A background action whose system is down for good (docs/host-contract.md, section 2):
    the kitchen never answers `notify_kitchen` for the even users. After the order the user
    goes back to the menu and opens the catalog. By the contract the action goes to the failure
    queue with an alert and the user is not held up; a host that runs it in the user's queue
    (as a sequential action) blocks the user."""
    random.seed(3)
    engine = Engine(engine_path, doc, processes=2)
    plans = [f"b{u}" for u in range(users)]
    broken = {b for i, b in enumerate(plans) if i % 2 == 0}
    host = Host(engine, crash_rate, background_queue=background_queue, kitchen=Kitchen(broken=broken))
    host.start_executor()
    sent = set()
    ends = {}

    def user_stream(user):
        after = [("cmd:start", None), ("btn:order", None)]
        ends[user] = run_user(host, user, order_policy([GOOD], after=after), sent)

    with ThreadPoolExecutor(8) as pool:
        list(pool.map(user_stream, plans))
    host.finish()
    at = {u: host.db.users[u]["snapshot"]["at"] for u in plans}
    blocked = sorted(u for u in plans if ends[u] == "blocked")
    free = sum(1 for u in plans if ends[u] == "finished" and at[u] == "catalog")
    failed = sorted(e["user"] for e in host.db.failures if e["action"].get("call") == "notify_kitchen")
    other = [e for e in host.db.failures if e["action"].get("call") != "notify_kitchen"]
    pending = len(host.db.pending(None, False)) + len(host.db.inbox_rows())
    orders = len(host.crm.orders())
    engine.close()
    name = "host by the contract" if background_queue else "host that runs background actions in the user's queue"
    print(f"background actions ({name}), failures injected {host.crashes}: users {users}, kitchen down for "
          f"{len(broken)}, users not held up {free}, blocked {len(blocked)}, orders {orders}, failure queue "
          f"{len(failed)} notify_kitchen with an alert, other failures {len(other)}, sequential actions not run {pending}")
    return (free == users and not blocked and failed == sorted(broken) and not other and pending == 0
            and orders == users and all(e.get("alert") for e in host.db.failures))


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


ORDER = ["cmd:start", "btn:order", "btn:pizza", "btn:size", "btn:add",
         "btn:checkout", "msg:text", "payment:success"]
SECOND = ["btn:order", "btn:pizza", "btn:size", "btn:add", "btn:checkout", "msg:text", "payment:success"]


def plan_lost(u):
    """A user whose snapshot is lost (the row only: update marks, data and outbox survive).
    Even users: an order, the loss, then a button of an old menu message (`btn:order`) and a
    second order to another address. Odd users: the loss at the cart while the reminder runs,
    the old timer fires while there is no snapshot, then `btn:order` from an old message and
    the order. The cart is never cleared, so every order after the loss has 2 pizzas. After
    `payment:success` the user waits for the outcome of `create_order`."""
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
                    for _ in range(50):              # the user's actions have run (a failure: again)
                        try:
                            host.drain(user)
                            break
                        except Crash:
                            pass
                    old_token[user] = host.timers.get((user, "cart_reminder"))
                    with host.db._lock:
                        host.db.users[user]["snapshot"] = None
                continue
            if ev == "TIMER-OLD":
                ev, token = "timer:cart_reminder", old_token[user]
            uid = f"timer:cart_reminder:{token}" if token is not None else i
            with ThreadPoolExecutor(2) as pool:
                list(pool.map(lambda _: deliver(host, user, uid, ev, token, payload), range(2)))
            if ev == "payment:success":
                settle(host, user)

    with ThreadPoolExecutor(8) as pool:
        list(pool.map(user_stream, range(users)))
    host.finish()
    orders = host.crm.orders()
    wrong = []
    for u in range(users):
        user = f"l{u}"
        got = sorted((o[2]["cart"], o[2]["address"]) for o in orders if o[0] == user)
        snap = host.db.users[user]["snapshot"]
        if snap is None or snap["at"] != "done" or got != sorted(plan_lost(u)[1]):
            wrong.append((user, snap and snap["at"], got))
    pending = len(host.db.pending()) + len(host.db.inbox_rows())
    engine.close()
    print(f"lost snapshot ({host_args or 'host by the contract'}), failures injected {host.crashes}: "
          f"users {users}, not as expected {len(wrong)}, actions not run {pending}")
    if wrong:
        print(f"  not as expected: {wrong[:4]}{' …' if len(wrong) > 4 else ''}")
    return not wrong and pending == 0


def control(what, run, attempts=8):
    """A probabilistic control: up to `attempts` runs, one red run is enough."""
    for attempt in range(attempts):
        if not run():
            print(f"  the test catches the violation: yes (control attempts: {attempt + 1})")
            return True
    print(f"  the test catches the violation: FAIL ({what})")
    return False


if __name__ == "__main__":
    engine_path, doc = sys.argv[1], sys.argv[2]
    barrier = "--no-barrier" not in sys.argv[3:]
    ok1 = scenario_orders(engine_path, doc, barrier=barrier)
    ok2 = scenario_stale_timer(engine_path, doc)
    print("--- control: a host without duplicate filtering and without idempotency keys")
    # The controls are probabilistic (a duplicate order or a lost outcome happens only with a
    # failure at the right place; one attempt catches it most of the time): up to eight
    # attempts, one red one is enough. The main scenario is not repeated.
    ok2 = control("duplicate orders", lambda: scenario_orders(engine_path, doc, naive=True)) and ok2
    print("--- control: a host that records the outcome of an action outside the transaction that marks it executed")
    ok2 = control("lost outcomes", lambda: scenario_orders(engine_path, doc, atomic_outcome=False)) and ok2
    print("--- control: a host that delivers `failed` for a call whose answer was lost, without reconciling it")
    ok2 = control("duplicate orders after a lost answer",
                  lambda: scenario_orders(engine_path, doc, reconcile=False), attempts=1) and ok2
    ok3 = scenario_background(engine_path, doc)
    print("--- control: a host that runs background actions in the user's queue, as sequential actions")
    background_control = scenario_background(engine_path, doc, background_queue=False)
    print("  the test catches the violation:", "yes" if not background_control else "FAIL")
    ok4 = scenario_lost_snapshot(engine_path, doc)
    print("--- controls: a host that starts a user without a snapshot without the update's event, "
          "or without the high-water mark")
    lost_controls = [scenario_lost_snapshot(engine_path, doc, first_event=False),
                     scenario_lost_snapshot(engine_path, doc, hwm=False)]
    print("  the test catches the violations:", "yes" if not any(lost_controls) else "FAIL")
    ok2 = ok2 and ok3 and not background_control and ok4 and not any(lost_controls)
    print("contract holds" if ok1 and ok2 else "CONTRACT VIOLATED")
    sys.exit(0 if ok1 and ok2 else 1)

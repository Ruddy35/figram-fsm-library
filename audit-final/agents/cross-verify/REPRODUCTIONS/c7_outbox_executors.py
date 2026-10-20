#!/usr/bin/env python3
"""C7 (cross-verify, independent): outbox executor topology vs HOST.md §2/§3.

Part 1 - several executors that do NOT partition by user (a common way to scale
"a separate executor"): the self-loop btn:self on `home` in examples/features.bot.json
returns [cancel_timer idle, start_timer idle token=n, send home] (real engine reply).
If the cancel is executed after the start (two executors, different latencies) the
scheduler ends up with NO timer although the snapshot says idle is active
-> the reminder is lost (the engine cannot help: no event ever arrives).
  1a deterministic interleaving (exec A slow on cancel, exec B fast on start)
  1b random stress: 40 users x 5 self-loops, 4 executors, random latency 0-20 ms
  CONTROL: the same stress with executors partitioned by user (hash(user) -> executor)

Part 2 - strict FIFO (one executor, "по порядку") and one user's call that fails
permanently (CRM answers 4xx forever): the executor retries the head forever and
no other user's action runs (head-of-line blocking).
  CONTROL: per-user FIFO queues (one failing user blocks only himself).

Usage: c7_outbox_executors.py ENGINE FEATURES_DOC PIZZA_DOC
"""
import os
import queue
import random
import sys
import threading
import time
import zlib

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "ffi", "python-example"))
from figram_client import Engine  # noqa: E402

ALL_TRUE = lambda ns: {n: True for n in ns}  # noqa: E731


class Scheduler:
    """HOST.md §3: scheduled events stored by (user, name); start replaces, cancel removes."""

    def __init__(self):
        self.lock = threading.Lock()
        self.timers = {}

    def apply(self, user, a):
        with self.lock:
            if "start_timer" in a:
                self.timers[(user, a["start_timer"])] = a["token"]
            elif "cancel_timer" in a:
                self.timers.pop((user, a["cancel_timer"]), None)


def user_steps(eng, user, n_loops):
    """Real engine replies for start + n self-loops; returns (outbox entries, final snapshot)."""
    r = eng.start(user)
    entries = [(user, r["snapshot"].get("seq", 0), i, a) for i, a in enumerate(r["actions"])]
    snap = r["snapshot"]
    for _ in range(n_loops):
        r = eng.step(user, snap, "btn:self", ALL_TRUE)
        snap = r["snapshot"]
        entries += [(user, snap["seq"], i, a) for i, a in enumerate(r["actions"])]
    return entries, snap


def run_executors(entries, n_exec, partition, latency):
    sched = Scheduler()
    if partition:
        qs = [queue.Queue() for _ in range(n_exec)]
        for e in entries:
            qs[zlib.crc32(e[0].encode()) % n_exec].put(e)
    else:
        q = queue.Queue()
        for e in entries:
            q.put(e)
        qs = [q] * n_exec

    def worker(q):
        while True:
            try:
                user, seq, i, a = q.get_nowait()
            except queue.Empty:
                return
            time.sleep(latency(a))
            sched.apply(user, a)

    ts = [threading.Thread(target=worker, args=(qs[k],)) for k in range(n_exec)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    return sched


def part1(eng):
    ok = True
    # 1a: deterministic, one user, one self-loop
    entries, snap = user_steps(eng, "alice", 1)
    tail = [e for e in entries if e[1] == snap["seq"]]
    print(f"[1a] engine reply for btn:self: {[e[3] for e in tail]}; snapshot timers={snap.get('timers')}")
    sched = Scheduler()
    for e in entries:
        if e[1] != snap["seq"]:
            sched.apply(e[0], e[3])       # earlier steps executed already
    cancel = next(e for e in tail if "cancel_timer" in e[3])
    start = next(e for e in tail if "start_timer" in e[3])
    ta = threading.Thread(target=lambda: (time.sleep(0.10), sched.apply(cancel[0], cancel[3])))  # exec A: slow
    tb = threading.Thread(target=lambda: (time.sleep(0.01), sched.apply(start[0], start[3])))    # exec B: fast
    ta.start(); tb.start(); ta.join(); tb.join()
    lost = ("alice", "idle") not in sched.timers
    print(f"[1a] two executors, cancel slower than start: scheduler has idle? {not lost} "
          f"(snapshot expects token {snap['timers']['idle']}) -> {'REMINDER LOST' if lost else 'ok'}")
    ok &= lost
    # 1b: stress
    random.seed(7)
    all_entries, finals = [], {}
    for u in range(40):
        es, sn = user_steps(eng, f"u{u}", 5)
        all_entries += es
        finals[f"u{u}"] = sn
    lat = lambda a: random.uniform(0, 0.02)  # noqa: E731
    for partition in (False, True):
        sched = run_executors(all_entries, 4, partition, lat)
        lost = sum(1 for u, sn in finals.items() if sched.timers.get((u, "idle")) != sn["timers"]["idle"])
        label = "CONTROL partitioned by user" if partition else "4 executors, shared FIFO   "
        print(f"[1b] {label}: users whose reminder is missing/stale in the scheduler: {lost} of {len(finals)}")
        ok &= (lost == 0) if partition else (lost > 0)
    return ok


def part2(eng):
    # Users: "bad" reaches payment:success first; create_order fails forever. Then 10 users get /start.
    path = ["/start", "btn:order", "btn:pizza", "btn:size", "btn:add", "btn:checkout", "msg:text", "payment:success"]
    snap, outbox = None, []
    for ev in path:
        r = eng.start("bad") if snap is None else eng.step("bad", snap, ev, ALL_TRUE)
        snap = r["snapshot"]
        if ev == "payment:success":
            outbox += [("bad", a) for a in r["actions"]]
    for u in range(10):
        r = eng.start(f"good{u}")
        outbox += [(f"good{u}", a) for a in r["actions"]]
    print(f"[2] outbox head: {outbox[:2]} ... then {len(outbox) - 2} actions of 10 other users")

    def execute(user, a):
        if a.get("call") == "create_order" and user == "bad":
            raise RuntimeError("CRM 400: invalid address")      # permanent failure
        return True

    def strict_fifo(deadline):
        done = []
        i = 0
        while time.monotonic() < deadline and i < len(outbox):
            try:
                execute(*outbox[i])
                done.append(outbox[i][0]); i += 1          # mark done, move on
            except RuntimeError:
                time.sleep(0.05)                           # retry the head (no dead-letter in HOST.md)
        return done

    def per_user(deadline):
        by_user = {}
        for u, a in outbox:
            by_user.setdefault(u, []).append(a)
        done = []
        for u, acts in by_user.items():
            for a in acts:
                if time.monotonic() >= deadline:
                    break
                try:
                    execute(u, a); done.append(u)
                except RuntimeError:
                    break                                   # this user's queue stalls, others go on
        return done

    d1 = strict_fifo(time.monotonic() + 2.0)
    d2 = per_user(time.monotonic() + 2.0)
    others1 = sum(1 for u in d1 if u != "bad")
    others2 = sum(1 for u in d2 if u != "bad")
    total_others = sum(1 for u, _ in outbox if u != "bad")
    print(f"[2] strict global FIFO, 2 s: actions of OTHER users executed: {others1} of {total_others}")
    print(f"[2] CONTROL per-user FIFO, 2 s: actions of OTHER users executed: {others2} of {total_others}")
    return others1 == 0 and others2 == total_others


def main():
    engine_bin, features, pizza = sys.argv[1:4]
    print(f"engine: {engine_bin}")
    eng = Engine(engine_bin, features)
    ok1 = part1(eng)
    eng.close()
    eng = Engine(engine_bin, pizza)
    ok2 = part2(eng)
    eng.close()
    print(f"C7 part1 (reorder loses reminders) {'REPRODUCED' if ok1 else 'NOT REPRODUCED'}; "
          f"part2 (HOL blocking) {'REPRODUCED' if ok2 else 'NOT REPRODUCED'}")
    sys.exit(0 if ok1 and ok2 else 1)


if __name__ == "__main__":
    main()

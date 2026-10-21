"""Load test of figram-engine from many Python threads.

Every reference scenario (from `figram trace`, derived from the proved
semantics of the chart) is one user. The threads replay the scenarios of
different users in parallel through a shared process pool and compare EVERY
step with the reference: the snapshot, the actions and the list of needed
conditions.

    python3 loadtest.py <figram-engine> <bot.json> <traces.jsonl> <threads> <processes>
"""

import json
import os
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "clients", "python"))
from figram_client import Engine

binary, document, traces_path = sys.argv[1:4]
threads = int(sys.argv[4])
processes = int(sys.argv[5])

traces = [json.loads(line) for line in open(traces_path, encoding="utf-8")]
engine = Engine(binary, document, processes)

lock = threading.Lock()
next_trace = iter(range(len(traces)))
stats = {"steps": 0, "need_rounds": 0, "mismatch": 0, "examples": []}
latencies: list[float] = []


def fail(msg: str) -> None:
    with lock:
        stats["mismatch"] += 1
        if len(stats["examples"]) < 5:
            stats["examples"].append(msg)


errors: list[str] = []


def worker() -> None:
    try:
        _worker()
    except Exception as e:  # noqa: BLE001 — an error in a thread fails the test
        with lock:
            errors.append(repr(e))


def _worker() -> None:
    local_lat = []
    local_steps = local_need = 0
    while True:
        with lock:
            i = next(next_trace, None)
        if i is None:
            break
        trace, user = traces[i], f"user-{i}"
        r = engine.start(user)
        if r["snapshot"] != trace["start"]["snapshot"] or r["actions"] != trace["start"]["actions"]:
            fail(f"{user}: start")
        snapshot = r["snapshot"]
        for n, st in enumerate(trace["steps"]):
            asked = []

            def guards(names, st=st, asked=asked):
                asked.extend(names)
                return {k: st["guards"][k] for k in names}

            t0 = time.perf_counter()
            r = engine.step(user, snapshot, st["event"], guards, st.get("token"))
            local_lat.append(time.perf_counter() - t0)
            local_steps += 1
            if asked:
                local_need += 1
            if sorted(asked) != sorted(st["guards"]):
                fail(f"{user} step {n}: conditions {asked} ≠ {list(st['guards'])}")
            if r["snapshot"] != st["snapshot"] or r["actions"] != st["actions"]:
                fail(f"{user} step {n}: {r} ≠ {st}")
            snapshot = r["snapshot"]
    with lock:
        stats["steps"] += local_steps
        stats["need_rounds"] += local_need
        latencies.extend(local_lat)


t0 = time.perf_counter()
pool = [threading.Thread(target=worker) for _ in range(threads)]
for t in pool:
    t.start()
for t in pool:
    t.join()
wall = time.perf_counter() - t0
engine.close()

latencies.sort() if latencies else latencies.append(0.0)
p = lambda q: latencies[min(len(latencies) - 1, int(q * len(latencies)))] * 1e6
print(
    f"threads {threads:3d}, processes {processes}: "
    f"steps {stats['steps']} (with a conditions request {stats['need_rounds']}), "
    f"mismatches {stats['mismatch']}, "
    f"{stats['steps'] / wall:,.0f} steps/s, "
    f"latency p50 {p(0.5):,.0f} µs, p99 {p(0.99):,.0f} µs"
)
for e in stats["examples"]:
    print("  ", e)
for e in errors[:5]:
    print("   thread error:", e)
expected = sum(len(t["steps"]) for t in traces)
failed = stats["mismatch"] or errors or stats["steps"] != expected or stats["steps"] == 0
if stats["steps"] != expected:
    print(f"   steps done {stats['steps']} of {expected}")
sys.exit(1 if failed else 0)

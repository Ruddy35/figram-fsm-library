"""CPU time of one request with a large `pending` (protocol 3), by the method of
tests/engine/snapshot_scaling.py: an engine process that answers only that request, minus the
start-up; sizes N and 4N; the ratio should be about 4 for linear time (16 for quadratic).
Also a request close to the default 1 MiB line limit.

usage: pending_scaling.py ENGINE examples/features.bot.json [N]
"""
import json, os, resource, subprocess, sys, time

engine, doc = sys.argv[1:3]
N = int(sys.argv[3]) if len(sys.argv) > 3 else 15000


def waits(n, call="ping", base=10):
    return [{"call": call, "token": base + i} for i in range(n)]


def snap(at, pend, seq):
    return {"at": at, "pending": pend, "seq": seq}


CASES = [
    ("accepted outcome, its wait last (every: ping)",
     lambda n: {"op": "step", "snapshot": snap("home", waits(n), n + 10), "event": "call:ping:pong", "token": n + 9}),
    ("accepted outcome, no arrow (check_stock wait last)",
     lambda n: {"op": "step", "snapshot": snap("home", waits(n) + [{"call": "check_stock", "token": 5}], n + 10),
                "event": "call:check_stock:yes", "token": 5}),
    ("stale outcome (no wait with that token)",
     lambda n: {"op": "step", "snapshot": snap("home", waits(n), n + 10), "event": "call:ping:pong", "token": 1}),
    ("new call, every (btn:ping on home)",
     lambda n: {"op": "step", "snapshot": snap("home", waits(n), n + 10), "event": "btn:ping"}),
    ("new call, latest (btn:check on item, N waits of check_stock? no: of ping)",
     lambda n: {"op": "step", "snapshot": snap("item", waits(n), n + 10), "event": "btn:check"}),
    ("latest replaces among N waits of the same action",
     lambda n: {"op": "step", "snapshot": snap("item", waits(n, "check_stock"), n + 10), "event": "btn:check"}),
    ("move keeps the waits (btn:go)",
     lambda n: {"op": "step", "snapshot": snap("home", waits(n), n + 10), "event": "btn:go", "guards": {"is_vip": True}}),
    ("recovery with an accepted outcome",
     lambda n: {"op": "step", "snapshot": snap("gone", waits(n), n + 10), "event": "call:ping:pong", "token": n + 9}),
    ("restart clears the waits",
     lambda n: {"op": "restart", "snapshot": snap("home", waits(n), n + 10)}),
    ("N copies of the same wait, accepted",
     lambda n: {"op": "step", "snapshot": snap("home", [{"call": "ping", "token": 7}] * n, n + 10),
                "event": "call:ping:pong", "token": 7}),
]


def encode(req):
    return (json.dumps(req, separators=(",", ":")) + "\n").encode()


def cpu(data):
    before = resource.getrusage(resource.RUSAGE_CHILDREN)
    p = subprocess.run([engine, "--max-request", "0", doc], input=data, capture_output=True, timeout=120)
    after = resource.getrusage(resource.RUSAGE_CHILDREN)
    return (after.ru_utime - before.ru_utime) + (after.ru_stime - before.ru_stime), p.stdout


base, _ = cpu(b"")
bad = 0
for what, mk in CASES:
    res = []
    for n in (N, 4 * N):
        data = encode(mk(n))
        t = min(cpu(data)[0] for _ in range(2)) - base
        out = json.loads(cpu(data)[1])
        res.append((n, len(data), max(t, 1e-3), out))
    (n1, b1, t1, o1), (n2, b2, t2, o2) = res
    ratio = t2 / t1
    pend = len(o2.get("snapshot", {}).get("pending", [])) if "snapshot" in o2 else o2
    flag = "" if ratio <= 8 and t2 <= 3 else "  <-- NOT LINEAR?"
    bad += bool(flag)
    print(f"{what}: N={n1} ({b1} B) {t1:.3f}s, 4N={n2} ({b2} B) {t2:.3f}s, ratio {ratio:.1f}; "
          f"reply pending {pend}{flag}")

# near the default 1 MiB limit (the engine's default --max-request)
p = subprocess.Popen([engine, doc], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
lo, hi = 1, 60000
def size(n):
    return len(encode({"id": 1, "op": "step", "snapshot": snap("home", waits(n), 10 ** 6), "event": "call:ping:pong", "token": 10 + n - 1}))
while lo < hi:
    mid = (lo + hi + 1) // 2
    if size(mid) - 1 <= 1048576:
        lo = mid
    else:
        hi = mid - 1
for n in (lo, lo + 1):
    line = encode({"id": n, "op": "step", "snapshot": snap("home", waits(n), 10 ** 6), "event": "call:ping:pong", "token": 10 + n - 1})
    t0 = time.monotonic()
    p.stdin.write(line); p.stdin.flush()
    r = json.loads(p.stdout.readline())
    dt = time.monotonic() - t0
    kind = "result, pending %d" % len(r["snapshot"].get("pending", [])) if "snapshot" in r else r.get("code")
    print(f"near the limit: {n} waits, line {len(line) - 1} bytes: {kind} in {dt:.3f}s wall")
p.stdin.close(); p.wait(10)
print("pending scaling:", "linear" if bad == 0 else f"{bad} cases not linear")
sys.exit(1 if bad else 0)

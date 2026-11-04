#!/usr/bin/env python3
"""A user of points.bot.json presses the `every` button `earn` while the loyalty service is slow.
Each accepted request adds a wait to the reply snapshot. The engine answers a request that fits
the request limit with a snapshot whose next request does not; from then on every request with
the stored snapshot -- the outcome that would remove a wait included, and `restart` -- is
`too_large`: the user is wedged until the host raises the process-wide limit.
Usage: limit_wedge.py ENGINE BOT"""
import json, subprocess, sys
eng, bot = sys.argv[1], sys.argv[2]
LIMIT = 1048576
seq = 9_000_000_000
def snap(n):
    return {"at": "home", "pending": [{"call": "award", "token": seq - i} for i in range(n)], "seq": seq}
def line(req):
    return json.dumps(req, separators=(",", ":"))
lo, hi = 1000, 60000
while lo < hi:  # the largest number of waits whose `earn` request fits
    mid = (lo + hi + 1) // 2
    if len(line({"id": 1, "op": "step", "snapshot": snap(mid), "event": "btn:earn"}).encode()) <= LIMIT:
        lo = mid
    else:
        hi = mid - 1
n = lo
r1 = line({"id": 1, "op": "step", "snapshot": snap(n), "event": "btn:earn"})
p = subprocess.run([eng, bot], input=(r1 + "\n").encode(), capture_output=True)
rep = json.loads(p.stdout.decode().splitlines()[0])
s2 = rep["snapshot"]
print(f"request 1: earn with {n} waits, {len(r1)} bytes (limit {LIMIT}) -> snapshot with {len(s2['pending'])} waits")
oldest = s2["pending"][-1]["token"]
reqs = [
    {"id": 2, "op": "step", "snapshot": s2, "event": "call:award:ok", "token": oldest},
    {"id": 3, "op": "step", "snapshot": s2, "event": "call:award:failed", "token": s2["pending"][0]["token"]},
    {"id": 4, "op": "restart", "snapshot": s2},
]
inp = "".join(line(r) + "\n" for r in reqs)
p = subprocess.run([eng, bot], input=inp.encode(), capture_output=True)
for r, out in zip(reqs, p.stdout.decode().splitlines()):
    print(f"request {r['id']} ({r.get('event', r['op'])}): {len(line(r))} bytes -> {out}")
ok = all('"too_large"' in o for o in p.stdout.decode().splitlines())
print("WEDGED: every request with the snapshot the engine wrote is too_large" if ok else "not wedged")
sys.exit(1 if ok else 0)

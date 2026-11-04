#!/usr/bin/env python3
"""A user presses an `every` button while no outcome comes: each step adds a wait. The engine must
refuse the step whose new snapshot would not fit into a request (snapshot_too_large) and keep
answering requests that carry the last snapshot it did write: the outcome and restart.
Exit 0: never wedged. Usage: limit_guard.py ENGINE BOT"""
import json, subprocess, sys
eng, bot = sys.argv[1], sys.argv[2]
LIMIT = 1048576
seq = 9_000_000_000
def line(req):
    return (json.dumps(req, separators=(",", ":")) + "\n").encode()
p = subprocess.Popen([eng, bot], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
def ask(req):
    p.stdin.write(line(req)); p.stdin.flush()
    return json.loads(p.stdout.readline())
stored = {"at": "home", "pending": [{"call": "award", "token": seq - i} for i in range(26900)], "seq": seq}
steps = 0
while True:
    req = {"id": steps, "op": "step", "snapshot": stored, "event": "btn:earn"}
    if len(line(req)) - 1 > LIMIT:
        print(f"WEDGED: the engine wrote a snapshot ({len(stored['pending'])} waits) whose next request is over the limit")
        sys.exit(1)
    rep = ask(req)
    if "snapshot" not in rep:
        break
    stored = rep["snapshot"]; steps += 1
print(f"after {steps} steps: {rep.get('code')} ({rep.get('error')})")
size = len(line(stored)) - 1
print(f"last stored snapshot: {len(stored['pending'])} waits, {size} bytes, limit {LIMIT}")
out = ask({"id": "o", "op": "step", "snapshot": stored, "event": "call:award:ok", "token": stored["pending"][-1]["token"]})
rst = ask({"id": "r", "op": "restart", "snapshot": stored})
print("outcome:", "snapshot with %d waits" % len(out["snapshot"]["pending"]) if "snapshot" in out else out)
print("restart:", "snapshot with %d waits" % len(rst["snapshot"].get("pending", [])) if "snapshot" in rst else rst)
p.stdin.close(); p.wait()
ok = rep.get("code") == "snapshot_too_large" and "snapshot" in out and "snapshot" in rst and size + LIMIT // 16 <= LIMIT
print("not wedged: a growing step is refused, the outcome and restart work" if ok else "UNEXPECTED")
sys.exit(0 if ok else 1)

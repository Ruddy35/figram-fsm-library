#!/usr/bin/env python3
"""RSS of the figram-engine process while it answers the same request repeatedly.
Usage: engine_rss.py ENGINE BOT WAITS COUNT"""
import json, subprocess, sys
eng, bot, n, count = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4])
seq = 5_000_000
snap = {"at": "home", "seq": seq}
if n:
    snap["pending"] = [{"call": "award", "token": seq - i} for i in range(n)]
req = {"id": 1, "op": "step", "snapshot": snap}
req.update({"event": "call:award:ok", "token": seq} if n else {"event": "btn:earn"})
line = (json.dumps(req, separators=(",", ":")) + "\n").encode()
p = subprocess.Popen([eng, bot], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
def rss():
    for l in open(f"/proc/{p.pid}/status"):
        if l.startswith("VmRSS:"):
            return int(l.split()[1])
first = None
for i in range(1, count + 1):
    p.stdin.write(line); p.stdin.flush()
    out = p.stdout.readline()
    assert b'"snapshot"' in out, out[:200]
    if i in (10, count // 4, count // 2, 3 * count // 4, count):
        r = rss()
        first = first or r
        print(f"requests={i} request_bytes={len(line)} VmRSS={r} KB")
p.stdin.close(); p.wait()
print(f"growth after the 10th request: {rss_end - first if (rss_end := r) else 0} KB, {1024*(r-first)/(count-10):.0f} bytes/request")

#!/usr/bin/env python3
"""Negative control: a broken engine that forgets the wait recorded by the current step
(drops every pending entry whose token equals the new seq)."""
import json, subprocess, sys
REAL = "/home/user/figram-audit-worktrees/cleanup-int/.lake/build/bin/figram-engine"
p = subprocess.Popen([REAL] + sys.argv[1:], stdin=sys.stdin, stdout=subprocess.PIPE)
for line in p.stdout:
    r = json.loads(line)
    snap = r.get("snapshot")
    if isinstance(snap, dict) and "pending" in snap:
        snap["pending"] = [w for w in snap["pending"] if w["token"] != snap["seq"]]
        if not snap["pending"]:
            del snap["pending"]
    sys.stdout.write(json.dumps(r, separators=(",", ":")) + "\n")
    sys.stdout.flush()
sys.exit(p.wait())

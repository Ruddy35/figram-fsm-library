#!/usr/bin/env python3
"""A broken engine for the negative controls of tests/engine/snapshot_scaling.py (the cases with
waits added after the first audit). It runs the real engine and damages its replies:

  SCALING_REAL=<figram-engine> SCALING_DEFECT=<defect> scaling_filter.py [engine args...]

defects:
  drop-other-waits  remove the waits of other actions (w0, w1, ...) from the reply's pending
  keep-answered     keep the answered/replaced wait (check_stock, 1) in the reply's pending
  quadratic-waits   burn CPU time quadratic in the number of waits of the request (a quadratic engine)
  drop-new-wait     drop the wait the step's call records (an engine that forgets to record it)
"""
import json
import os
import subprocess
import sys

real = os.environ["SCALING_REAL"]
defect = os.environ["SCALING_DEFECT"]
data = sys.stdin.buffer.read()
out = subprocess.run([real] + sys.argv[1:], input=data, capture_output=True).stdout
lines = []
for raw, line in zip(data.splitlines(), out.splitlines()):
    r = json.loads(line)
    try:
        req = json.loads(raw)
    except ValueError:
        req = {}
    pend = req.get("snapshot", {}).get("pending", []) if isinstance(req, dict) else []
    if "snapshot" in r:
        if defect == "drop-other-waits" and pend:
            r["snapshot"]["pending"] = [w for w in r["snapshot"].get("pending", []) if not w["call"].startswith("w")]
        elif defect == "keep-answered" and any(w["call"] == "check_stock" for w in pend):
            r["snapshot"].setdefault("pending", []).append({"call": "check_stock", "token": 1})
        elif defect == "drop-new-wait" and pend:
            seq = r["snapshot"].get("seq")
            r["snapshot"]["pending"] = [w for w in r["snapshot"].get("pending", []) if w["token"] != seq]
            if not r["snapshot"]["pending"]:
                del r["snapshot"]["pending"]
            r["actions"] = [a for a in r["actions"]]
        elif defect == "quadratic-waits" and pend:
            n = len(pend) // 400
            x = 0
            for i in range(n * n * 400):
                x += i
    lines.append(json.dumps(r, separators=(",", ":")))
sys.stdout.write("\n".join(lines) + ("\n" if lines else ""))

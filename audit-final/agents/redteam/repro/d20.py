#!/usr/bin/env python3
"""d20.py ENGINE: start; the reminder `tick` fires (handled by an arrow without a target); then the
`timeout` timer fires with its token. Each step uses the snapshot the engine returned."""
import json, subprocess, sys, os
doc = os.path.join(os.path.dirname(os.path.abspath(__file__)), "d20.bot.json")
p = subprocess.Popen([sys.argv[1], doc], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
def ask(req):
    p.stdin.write(json.dumps(req) + "\n"); p.stdin.flush(); r = json.loads(p.stdout.readline()); print(json.dumps(r)); return r
r = ask({"id": 1, "op": "start"})
tok = {a["start_timer"]: a["token"] for a in r["actions"] if "start_timer" in a}
r = ask({"id": 2, "op": "step", "snapshot": r["snapshot"], "event": "timer:tick", "token": tok["tick"]})
r = ask({"id": 3, "op": "step", "snapshot": r["snapshot"], "event": "timer:timeout", "token": tok["timeout"]})
print("user moved to `gone` on timeout:", r["snapshot"]["at"] == "gone")
sys.exit(0 if r["snapshot"]["at"] == "gone" else 1)

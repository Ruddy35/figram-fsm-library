#!/usr/bin/env python3
"""H2: a reminder started under document v1 survives the upgrade to v2 forever
and its (valid) token later triggers a transition + developer call on a
message that has no reminder at all.

  python3 repro.py [ENGINE]     (default: worktree bin/figram-engine, CI-verified)
Exit 1 = bug reproduced (baseline), 0 = fixed."""
import json, os, subprocess, sys
here = os.path.dirname(os.path.abspath(__file__))
eng = sys.argv[1] if len(sys.argv) > 1 else "/home/user/figram-audit-worktrees/core-compiler/bin/figram-engine"

def session(doc, reqs):
    p = subprocess.run([eng, os.path.join(here, doc)], input="".join(json.dumps(r) + "\n" for r in reqs),
                       capture_output=True, text=True, timeout=30)
    return [json.loads(l) for l in p.stdout.splitlines()]

# 1. user starts under v1: A owns reminder "remind" (60 s)
r1 = session("v1.bot.json", [{"id": 1, "op": "start"}])[0]
print("v1 start   ->", json.dumps(r1, ensure_ascii=False))
s = r1["snapshot"]
# 2. host loads v2 (designer moved the reminder from A to B); user presses a button
def step(snap, ev, **kw):
    req = dict({"id": 1, "op": "step", "snapshot": snap, "event": ev}, **kw)
    r = session("v2.bot.json", [req])[0]
    print(f"v2 {ev:14s}->", json.dumps(r, ensure_ascii=False))
    return r
r2 = step(s, "btn:next")
r3 = step(r2["snapshot"], "btn:x")
# 3. the host's scheduler fires the v1 reminder (never cancelled: no cancel_timer was emitted)
r4 = step(r3["snapshot"], "timer:remind", token=r1["actions"][0]["token"])
bug = "remind" in r2["snapshot"].get("timers", {}) and r4["snapshot"]["at"] == "R"
print("BUG REPRODUCED: stale v1 reminder kept in snapshot and fired a transition with call k1 on D"
      if bug else "not reproduced")
sys.exit(1 if bug else 0)

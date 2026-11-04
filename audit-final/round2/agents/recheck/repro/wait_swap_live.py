#!/usr/bin/env python3
"""wait-swap:create_order is a live mutant of the pizza document: a user who leaves `placing`
(cmd:start) before the CRM answers and orders again calls create_order a second time while the
first wait is still pending; `latest` replaces the wait, `every` (the mutant) keeps both.

  python3 -I wait_swap_live.py ENGINE [MUTANT]   (ENGINE must accept --mutate MUTANT with
                                                  FIGRAM_ALLOW_MUTANTS=1 when MUTANT is given)
Prints the pending list after the second create_order; exit 0 iff it holds one wait."""
import json
import os
import subprocess
import sys

engine = sys.argv[1]
mutant = sys.argv[2] if len(sys.argv) > 2 else None
doc = os.path.join(os.path.dirname(__file__), "..", "..", "..", "figram-audit-worktrees", "a6-recheck",
                   "examples", "pizza.bot.json")
doc = os.environ.get("PIZZA", doc)
cmd = [engine] + (["--mutate", mutant] if mutant else []) + [doc]
p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
                     env=dict(os.environ, FIGRAM_ALLOW_MUTANTS="1"))
rid = 0


def req(o):
    global rid
    rid += 1
    o["id"] = rid
    p.stdin.write(json.dumps(o) + "\n")
    p.stdin.flush()
    r = json.loads(p.stdout.readline())
    assert "snapshot" in r, r
    return r


G = {"cart_not_empty": True, "in_delivery_zone": True, "address_rejected": False}
s = req({"op": "start"})["snapshot"]
order = ["btn:order", "btn:pizza", "btn:size", "btn:add", "btn:checkout", "msg:text", "payment:success"]
for ev in order:
    s = req({"op": "step", "snapshot": s, "event": ev, "guards": G})["snapshot"]
first = list(s.get("pending", []))
s = req({"op": "step", "snapshot": s, "event": "cmd:start"})["snapshot"]
for ev in order:
    s = req({"op": "step", "snapshot": s, "event": ev, "guards": G})["snapshot"]
print(f"at {s['at']}: pending after the first call {first}, after the second {s.get('pending')}")
p.stdin.close()
p.wait()
sys.exit(0 if len(s.get("pending", [])) == 1 else 1)

import sys, json
sys.path.insert(0, "/home/user/figram-audit-worktrees/a5-semantics/clients/python")
from figram_client import Engine
B = "/home/user/figram-audit-worktrees/a5-semantics/.lake/build/bin/figram-engine"
D = "/home/user/figram-audit/agents5/semantics/REPRODUCTIONS/docs/outcomes.bot.json"
e = Engine(B, D)
try:
    snap = {"at": "gone", "pending": [{"call": "mk", "token": 2}], "seq": 2}
    r = e.outcome("u1", snap, "mk", "ok", 1)   # token 1: no wait for it
    print("stale outcome, snapshot at a removed message:", json.dumps(r))
    print("unchanged snapshot without actions?", r.get("snapshot") == snap and r.get("actions") == [])
finally:
    e.close()

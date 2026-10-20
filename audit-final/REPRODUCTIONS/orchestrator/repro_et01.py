"""Independent repro of ET-01 (orchestrator): idempotency keys user:seq:index (HOST.md §1.4)
repeat after `start` for an existing user, so a CRM deduplicating by key drops the 2nd order."""
import json, subprocess, sys
eng, doc = sys.argv[1], sys.argv[2]
p = subprocess.Popen([eng, doc], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
rid = 0
def req(**kw):
    global rid; rid += 1
    p.stdin.write(json.dumps({"id": rid, **kw}) + "\n"); p.stdin.flush()
    r = json.loads(p.stdout.readline()); assert r["id"] == rid, r
    return r
def step(snap, ev):
    r = req(op="step", snapshot=snap, event=ev)
    if "need" in r:
        r = req(op="step", snapshot=snap, event=ev, guards={g: True for g in r["need"]})
    assert "snapshot" in r, r
    return r
PATH = ["btn:order", "btn:pizza", "btn:size", "btn:add", "btn:checkout", "msg:text", "payment:success"]
def life(user, crm, outbox_keys):
    r = req(op="start"); snap = r["snapshot"]
    for i, a in enumerate(r["actions"]): outbox_keys.append(f"{user}:{snap.get('seq',0)}:{i}")
    for ev in PATH:
        r = step(snap, ev); snap = r["snapshot"]
        for i, a in enumerate(r["actions"]):
            key = f"{user}:{snap.get('seq',0)}:{i}"     # HOST.md §1.4
            outbox_keys.append(key)
            if "call" in a:
                if key in crm: print(f"  CRM dedup: {a['call']} with key {key} -> returns result of earlier {crm[key]}")
                else: crm[key] = a["call"]
    return snap
crm, k1, k2 = {}, [], []
print("life 1:"); s1 = life("u", crm, k1)
print("life 2 (start again, e.g. snapshot lost / 'delete my data'):"); s2 = life("u", crm, k2)
orders = [v for v in crm.values() if v == "create_order"]
print(f"final at: {s1['at']} / {s2['at']}; keys life1={len(k1)} life2={len(k2)} colliding={len(set(k1)&set(k2))}")
print(f"create_order executed in CRM: {len(orders)} (expected 2)")
p.stdin.close(); p.wait()
sys.exit(1 if len(orders) != 2 else 0)

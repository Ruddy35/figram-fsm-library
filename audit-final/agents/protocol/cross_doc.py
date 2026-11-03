"""Document update: snapshots written under document A are stepped under document B (random
events of B, outcome events of A's waits, timer events of A's timers). Every reply is validated
(protocol.check_reply, snapshot schema) and the waits must follow decision 12 / host-contract
section 6: an accepted outcome removes exactly its wait, anything else keeps all waits; recovery
cancels every snapshot timer first. usage: cross_doc.py REPO DUMP DOC..."""
import collections, json, os, random, subprocess, sys
import jsonschema
repo, dump, docs = sys.argv[1], sys.argv[2], sys.argv[3:]
sys.path.insert(0, os.path.join(repo, "clients", "python"))
import protocol  # noqa
SV = jsonschema.Draft202012Validator(json.load(open(os.path.join(repo, "schemas/snapshot.schema.json"))))
E = os.path.join(repo, ".lake/build/bin/figram-engine")
snaps = collections.defaultdict(list)
for l in open(dump):
    r = json.loads(l); rep = json.loads(r["line"])
    if "snapshot" in rep: snaps[r["doc"]].append(rep["snapshot"])
def events(node, acc):
    acc.update(node.get("on") or {}); [events(c, acc) for c in (node.get("children") or {}).values()]; return acc
rnd = random.Random(5)
bad = total = rec = acc_out = 0
def problem(m):
    global bad; bad += 1
    if bad < 10: print("PROBLEM", m)
for b in docs:
    evs = [e for e in events(json.load(open(b))["bot"], {}) if not e.startswith(("timer:", "call:"))] + ["btn:none"]
    p = subprocess.Popen([E, b], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    n = 0
    for a in docs:
        if a == b: continue
        for s in rnd.sample(snaps[a], min(60, len(snaps[a]))):
            n += 1; total += 1
            pend = [(w["call"], w["token"]) for w in s.get("pending", [])]
            x = rnd.random()
            if x < 0.4 and pend:
                c, t = rnd.choice(pend); q = {"op": "step", "snapshot": s, "event": f"call:{c}:{rnd.choice(['ok','no','done','failed','zz'])}", "token": t}
            elif x < 0.55 and s.get("timers"):
                t, tok = rnd.choice(sorted(s["timers"].items())); q = {"op": "step", "snapshot": s, "event": f"timer:{t}", "token": tok}
            elif x < 0.6:
                q = {"op": "restart", "snapshot": s}
            else:
                q = {"op": "step", "snapshot": s, "event": rnd.choice(evs)}
            q["id"] = n; q["guards"] = {f"c{i}": rnd.random() < .5 for i in range(1, 6)}
            if q["op"] == "restart": del q["guards"]
            p.stdin.write((json.dumps(q) + "\n").encode()); p.stdin.flush()
            r = json.loads(p.stdout.readline())
            try:
                v = protocol.check_reply(r, n, q["op"])
            except protocol.ProtocolError as e:
                problem(f"validator: {e} {r}"); continue
            if v != "result":
                if v == "need":   # conditions of B outside c1..c5 (examples)
                    continue
                problem(f"{q} -> {r}"); continue
            if not SV.is_valid(r["snapshot"]): problem(f"schema {r}")
            np_ = [(w["call"], w["token"]) for w in r["snapshot"].get("pending", [])]
            new = [(a_["call"], a_["token"]) for a_ in r["actions"] if "call" in a_ and "token" in a_]
            if q["op"] == "restart":
                if np_: problem(f"restart kept waits {r}")
                continue
            rec += bool(r.get("recovered"))
            ev = q["event"]
            if ev.startswith("call:"):
                c = ev[5:].rpartition(":")[0]
                expect = [w for w in pend if w != (c, q["token"])]
                acc_out += 1
            else:
                expect = pend
            kept = [w for w in np_ if w not in new]
            # a new latest call may replace a wait of the same action: allow removal only of those
            replaced = {cn for cn, _ in new}
            if sorted(set(kept)) != sorted(set(w for w in expect if w[0] not in replaced or w in np_)):
                problem(f"waits {pend} -> {np_} for {q['event']} (new {new})")
            if r.get("recovered") and s.get("timers"):
                cancels = [a_["cancel_timer"] for a_ in r["actions"] if "cancel_timer" in a_]
                if sorted(cancels[:len(s["timers"])]) != sorted(s["timers"]): problem(f"recovery cancels {r}")
    p.stdin.close(); p.wait(10)
print(f"cross-document: requests {total}, recovered {rec}, outcome events {acc_out}, problems {bad}")
sys.exit(1 if bad else 0)

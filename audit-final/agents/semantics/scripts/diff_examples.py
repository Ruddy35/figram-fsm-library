import json, random, subprocess, sys
sys.path.insert(0, "/home/user/figram-audit/agents5/semantics/scripts")
import prose_ref as P
EX = "/home/user/figram-audit-worktrees/a5-semantics/examples"
tot = bad = 0
for name in ["pizza", "features", "annotations"]:
    path = f"{EX}/{name}.bot.json"
    d = json.load(open(path)); D = P.Doc(d)
    msgs = D.messages; secs = [n for n in D.order if D.kind[n] == "sec" and n]
    timers = sorted({t for m in msgs for t in D.timers[m]})
    evs = sorted({e for n in D.order for e in D.on[n] if not e.startswith("timer:")}) + ["btn:zz"]
    # the generator draws actions from its own list; add this document's actions
    R = random.Random(7)
    acts = list(D.calls) + ["gone"]
    reqs = P.gen_requests(R, D, msgs, secs, timers, evs, 3000)
    for r in reqs:  # use this document's actions in pending
        if "snapshot" in r and "pending" in r["snapshot"]:
            for p in r["snapshot"]["pending"]:
                p["call"] = R.choice(acts)
        if r.get("event", "").startswith("call:"):
            c = r["event"][5:].rsplit(":", 1)[0]
            ws = [p["token"] for p in r["snapshot"].get("pending", []) if p["call"] == c]
            if ws and R.random() < 0.8: r["token"] = R.choice(ws)
        conds = list(d.get("conditions", {}))
        if "guards" in r and conds:
            r["guards"] = {c: R.random() < 0.5 for c in conds if R.random() < 0.8}
    inp = "".join(json.dumps(r) + "\n" for r in reqs)
    out = subprocess.run(["timeout", "60", "/home/user/figram-audit-worktrees/a5-semantics/.lake/build/bin/figram-engine", path],
                         input=inp, capture_output=True, text=True).stdout.splitlines()
    b = 0
    for r, a in zip(reqs, out):
        tot += 1
        e = P.reply(D, r); a = json.loads(a)
        if P.normalize(a) != P.normalize(e):
            b += 1
            if b <= 3: print("MISMATCH", name, json.dumps(r), "\n exp", json.dumps(e), "\n act", json.dumps(a))
    bad += b
    print(name, "requests", len(reqs), "mismatches", b, "replies", len(out))
print("total", tot, "mismatches", bad)
sys.exit(1 if bad else 0)

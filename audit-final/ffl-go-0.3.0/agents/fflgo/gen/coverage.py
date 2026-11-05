import json, sys, collections
c = collections.Counter()
for l in open(sys.argv[1]):
    e = json.loads(l); r = json.loads(e["line"]); req = e["req"]
    if "error" in r: c["error:" + r["code"] + (":idnull" if r["id"] is None else "")] += 1
    elif "need" in r: c["need:" + e["op"]] += 1
    elif "protocol" in r: c["hello"] += 1
    else:
        c["result:" + e["op"]] += 1
        if r.get("recovered"): c["recovered"] += 1
        s = r["snapshot"]
        if "pending" in s: c["snap.pending"] += 1
        if len(s.get("pending", [])) > 1: c["snap.pending>1"] += 1
        if len(s.get("timers", {})) > 1: c["snap.timers>1"] += 1
        if len(s.get("resume", {})) > 1: c["snap.resume>1"] += 1
        if "back" in s: c["snap.back"] += 1
        for a in r["actions"]:
            if "call" in a:
                k = "call" + ("+token" if "token" in a else "") + ("+detached" if "detached" in a else "")
                c[k] += 1
                if ":" in a["call"]: c["call name with ':'"] += 1
        if any(":" in w["call"] for w in s.get("pending", [])): c["pending name with ':'"] += 1
    ev = req.get("event", "")
    if ev.startswith("call:") and "error" not in r: c["req outcome event"] += 1
    if ev.startswith("call:") and "error" not in r and ev[5:].rsplit(":",1)[0].count(":"): c["req outcome, action with ':'"] += 1
    if ev.startswith("timer:") and "error" not in r: c["req timer event"] += 1
    c["total"] += 1
for k, v in sorted(c.items()): print(f"{k:35s} {v}")

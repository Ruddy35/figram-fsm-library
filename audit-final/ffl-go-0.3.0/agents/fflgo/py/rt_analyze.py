"""Classify round-trip differences (out/rt.jsonl) by the exact cause."""
import json, sys, collections
c = collections.Counter(); ex = {}
def pairs(s): return json.loads(s, object_pairs_hook=lambda kv: kv)
for l in open(sys.argv[1]):
    d = json.loads(l)
    g, w = pairs(d["got"]), pairs(d["want"])
    causes = []
    if d["part"] == "snapshot":
        gk, wk = [k for k, _ in g], [k for k, _ in w]
        if gk != wk: causes.append("snapshot key order")
        gd, wd = dict(g), dict(w)
        for f in ("resume", "timers"):
            if f in wd and [k for k, _ in gd[f]] != [k for k, _ in wd[f]]:
                causes.append(f"{f} key order")
        if gd.get("pending") and [list(dict(x).items()) for x in gd["pending"]] != [list(dict(x).items()) for x in wd["pending"]]:
            causes.append("pending differs")
    elif d["part"] == "action":
        if [k for k, _ in g] != [k for k, _ in w]: causes.append("action key order")
    elif d["part"] == "reply":
        if [k for k, _ in g] != [k for k, _ in w]: causes.append("reply key order " + ",".join(k for k, _ in g) + " vs " + ",".join(k for k, _ in w))
    raw_esc = any(e in d["got"] for e in ("\\u003c", "\\u003e", "\\u0026")) and not any(e in d["want"] for e in ("\\u003c", "\\u003e", "\\u0026"))
    if raw_esc: causes.append("HTML escaping \\u003c/\\u003e/\\u0026")
    key = d["part"] + ": " + (" + ".join(causes) or "UNEXPLAINED")
    c[key] += 1
    ex.setdefault(key, d)
for k, n in c.most_common():
    e = ex[k]
    print(f"{n:7d}  {k}\n         got  {e['got'][:230]}\n         want {e['want'][:230]}\n         ({e['k']})")

"""Mutate engine replies: python3 -I mutate.py valid.jsonl mut.jsonl [seed]

Each mutation is named; the output lines are {"k","op","id","line","mut","src"}. Every valid reply
gets one random applicable mutation; a stratified sample (per reply variant) gets all of them.
Raw number texts (1.0, -0, 1e0) are injected through a placeholder string.
"""
import json, random, sys, copy

MAXINT = 2**53 - 1
RAWP = "@@RAW:"


def raw(text):
    return RAWP + text + "@@"


def dumps(v):
    s = json.dumps(v, separators=(",", ":"), ensure_ascii=False)
    while RAWP in s:
        i = s.index('"' + RAWP)
        j = s.index('@@"', i + 1)
        s = s[:i] + s[i + 1 + len(RAWP):j] + s[j + 3:]
    return s


def variant(r):
    if "error" in r:
        return "error"
    if "need" in r:
        return "need"
    if "protocol" in r:
        return "hello"
    return "result"


def set_in(r, path, val, drop=False):
    r = copy.deepcopy(r)
    o = r
    for p in path[:-1]:
        o = o[p]
    if drop:
        del o[path[-1]]
    else:
        o[path[-1]] = val
    return r


def muts(r, op, rid):
    """Yield (name, obj-or-rawtext)."""
    v = variant(r)
    # top level
    for k in list(r):
        yield f"drop:{k}", set_in(r, [k], None, drop=True)
    for name, val in [("str", str(rid)), ("float", raw(f"{rid}.0")), ("exp", raw(f"{rid}e0")), ("plus1", rid + 1),
                      ("minus", -rid), ("null", None), ("true", True), ("big", 2**53 + rid)]:
        yield f"id:{name}", set_in(r, ["id"], val)
    yield "add:extra", dict(r, extra=1)
    if v == "result":
        for name, val in [("true", True), ("false", False), ("str", "true"), ("one", 1), ("null", None)]:
            yield f"recovered:{name}", dict(r, recovered=val)
        yield "result+need", dict(r, need=["c"])
        yield "result+error", dict(r, error="x")
        yield "result+code", dict(r, code="bad_request")
        for name, val in [("null", None), ("list", []), ("str", "x"), ("empty", {})]:
            yield f"snapshot:{name}", dict(r, snapshot=val)
        for name, val in [("null", None), ("obj", {}), ("str", "x")]:
            yield f"actions:{name}", dict(r, actions=val)
        s = r["snapshot"]
        sp = ["snapshot"]
        yield "snap:drop:at", set_in(r, sp + ["at"], None, drop=True)
        for name, val in [("empty", ""), ("int", 1), ("null", None)]:
            yield f"snap:at:{name}", set_in(r, sp + ["at"], val)
        for name, val in [("1", 1), ("2", 2), ("str", "1"), ("float", raw("1.0")), ("true", True), ("0", 0), ("negzero", raw("-0"))]:
            yield f"snap:v:{name}", set_in(r, sp + ["v"], val)
        for name, val in [("neg", -1), ("float", raw("1.5")), ("float0", raw("5.0")), ("exp", raw("5e0")), ("str", "1"),
                          ("over", 2**53), ("max", MAXINT), ("true", True), ("null", None), ("negzero", raw("-0")), ("zero", 0)]:
            yield f"snap:seq:{name}", set_in(r, sp + ["seq"], val)
        for name, val in [("empty", ""), ("null", None), ("int", 1), ("ok", "elsewhere")]:
            yield f"snap:back:{name}", set_in(r, sp + ["back"], val)
        for name, val in [("list", []), ("emptykey", {"": "x"}), ("emptyval", {"s": ""}), ("int", {"s": 1}), ("null", None), ("empty", {})]:
            yield f"snap:resume:{name}", set_in(r, sp + ["resume"], val)
        for name, val in [("emptykey", {"": 1}), ("neg", {"t": -1}), ("str", {"t": "1"}), ("float", {"t": raw("1.5")}),
                          ("float0", {"t": raw("1.0")}), ("over", {"t": 2**53}), ("true", {"t": True}), ("null", None),
                          ("list", []), ("empty", {}), ("negzero", {"t": raw("-0")})]:
            yield f"snap:timers:{name}", set_in(r, sp + ["timers"], val)
        w = (s.get("pending") or [{"call": "act", "token": 1}])[0]
        for name, val in [("empty", []), ("null", None), ("obj", {}), ("int", [1]), ("nested", [[w]]),
                          ("extra", [dict(w, x=1)]), ("nocall", [{"token": w["token"]}]), ("notoken", [{"call": w["call"]}]),
                          ("callempty", [dict(w, call="")]), ("callint", [dict(w, call=1)]), ("tokstr", [dict(w, token="1")]),
                          ("tokfloat", [dict(w, token=raw("1.5"))]), ("tokfloat0", [dict(w, token=raw("6.0"))]),
                          ("tokneg", [dict(w, token=-1)]), ("tokover", [dict(w, token=2**53)]), ("toktrue", [dict(w, token=True)]),
                          ("toknull", [dict(w, token=None)]), ("toknegzero", [dict(w, token=raw("-0"))]),
                          ("dup", [w, w]), ("reorder", [{"token": w["token"], "call": w["call"]}]), ("detached", [dict(w, detached=True)])]:
            yield f"snap:pending:{name}", set_in(r, sp + ["pending"], val)
        yield "snap:extra", set_in(r, sp + ["x"], 1)
        acts = r["actions"]
        base = acts[0] if acts else {"call": "act", "token": 1}
        bad_acts = [("unknown", {"jump": "x"}), ("emptyobj", {}), ("str", "send"), ("null", None), ("twokinds", {"send": "a", "call": "b"}),
                    ("send:empty", {"send": ""}), ("send:int", {"send": 1}), ("send:null", {"send": None}),
                    ("send+detached", {"send": "a", "detached": True}), ("send+token", {"send": "a", "token": 1}),
                    ("cancel+token", {"cancel_timer": "t", "token": 1}), ("call+outcome", {"call": "a", "token": 1, "outcome": "x"}),
                    ("call:tokstr", {"call": "a", "token": "1"}), ("call:tokfloat", {"call": "a", "token": raw("1.5")}),
                    ("call:tokfloat0", {"call": "a", "token": raw("6.0")}), ("call:tokneg", {"call": "a", "token": -1}),
                    ("call:tokover", {"call": "a", "token": 2**53}), ("call:toktrue", {"call": "a", "token": True}),
                    ("call:toknull", {"call": "a", "token": None}), ("call:toknegzero", {"call": "a", "token": raw("-0")}),
                    ("call:tokmax", {"call": "a", "token": MAXINT}), ("call:tok0", {"call": "a", "token": 0}),
                    ("call:detfalse", {"call": "a", "detached": False}), ("call:det1", {"call": "a", "detached": 1}),
                    ("call:detstr", {"call": "a", "detached": "true"}), ("call:detnull", {"call": "a", "detached": None}),
                    ("call:tok+detfalse", {"call": "a", "token": 1, "detached": False}), ("call:empty", {"call": ""}),
                    ("call:int", {"call": 1}), ("call:reorder", {"detached": True, "token": 1, "call": "a"}),
                    ("call:colon", {"call": "a:b:c", "token": 1}),
                    ("timer:noms", {"start_timer": "t", "token": 1}), ("timer:notoken", {"start_timer": "t", "ms": 1}),
                    ("timer:msneg", {"start_timer": "t", "ms": -1, "token": 1}), ("timer:msfloat", {"start_timer": "t", "ms": raw("1.5"), "token": 1}),
                    ("timer:msstr", {"start_timer": "t", "ms": "1", "token": 1}), ("timer:detached", {"start_timer": "t", "ms": 1, "token": 1, "detached": True}),
                    ("timer:empty", {"start_timer": "", "ms": 1, "token": 1}), ("cancel:empty", {"cancel_timer": ""})]
        for name, a in bad_acts:
            yield f"act:{name}", dict(r, actions=acts + [a])
        if acts:
            for k in list(base):
                yield f"act0:drop:{k}", dict(r, actions=[set_in(base, [k], None, drop=True)] + acts[1:])
    if v == "need":
        need = r["need"]
        for name, val in [("empty", []), ("emptystr", [""]), ("int", [1]), ("dup", need + [need[0]]), ("str", need[0]),
                          ("null", None), ("obj", {need[0]: True})]:
            yield f"need:{name}", dict(r, need=val)
        yield "need+snapshot", dict(r, snapshot={"at": "x"})
    if v == "hello":
        for name, val in [("2", 2), ("str", "3"), ("float", raw("3.0")), ("neg", -3), ("4", 4)]:
            yield f"hello:protocol:{name}", dict(r, protocol=val)
        for name, val in [("neg", -1), ("float", raw("1.5")), ("over", 2**53), ("str", "13"), ("zero", 0)]:
            yield f"hello:states:{name}", dict(r, states=val)
        for name, val in [("ok", "3:x"), ("empty", ""), ("int", 1)]:
            yield f"hello:mutant:{name}", dict(r, mutant=val)
    if v == "error":
        for name, val in [("unknown", "nonsense"), ("int", 7), ("null", None), ("each_bad_request", "bad_request"),
                          ("each_parse_error", "parse_error"), ("each_too_large", "too_large"), ("each_bad_snapshot", "bad_snapshot"),
                          ("each_seq_overflow", "seq_overflow"), ("each_snapshot_too_large", "snapshot_too_large")]:
            yield f"err:code:{name}", dict(r, code=val)
            if name.startswith("each_"):
                yield f"err:idnull:{val}", dict(r, code=val, id=None)
                yield f"err:idwrong:{val}", dict(r, code=val, id=rid + 1)
                yield f"err:idmissing:{val}", {"error": r["error"], "code": val}
        for name, val in [("int", 1), ("empty", ""), ("null", None)]:
            yield f"err:error:{name}", dict(r, error=val)
        yield "err:nocode", {"id": rid, "error": r["error"]}
    # raw text mutations
    t = dumps(r)
    for name, text in [("trail:x", t + " x"), ("trail:brace", t + "}"), ("trail:bracket", t + "]"), ("trail:ws", t + " \t"),
                       ("lead:ws", " " + t), ("bom", "﻿" + t), ("dupid", '{"id":%d,' % (rid + 1) + t[1:]),
                       ("dupid_last", t[:-1] + ',"id":%d}' % (rid + 1)), ("array", "[" + t + "]"), ("null", "null"), ("empty", ""),
                       ("nan", t[:-1] + ',"x":NaN}'), ("trail:obj", t + "{}"), ("trail:num", t + " 1"), ("truncated", t[:-1])]:
        yield f"raw:{name}", text


def main():
    src, dst = sys.argv[1], sys.argv[2]
    rng = random.Random(int(sys.argv[3]) if len(sys.argv) > 3 else 11)
    out = open(dst, "w")
    full = {}
    n = 0
    for line in open(src):
        e = json.loads(line)
        if e["k"].startswith("limit/"):
            continue
        r = json.loads(e["line"])
        v = variant(r)
        ms = list(muts(r, e["op"], e["id"]))
        key = (v, e["op"], bool(r.get("recovered")), "pending" in r.get("snapshot", {}))
        chosen = ms if full.get(key, 0) < 60 else [rng.choice(ms)]
        full[key] = full.get(key, 0) + 1
        for name, m in chosen:
            text = m if isinstance(m, str) else dumps(m)
            out.write(json.dumps({"k": f"{e['k']}#{name}", "op": e["op"], "id": e["id"], "line": text, "mut": name,
                                  "src": e["k"]}, ensure_ascii=False) + "\n")
            n += 1
    print("mutants", n, "full sweeps", sum(min(c, 60) for c in full.values()), "strata", len(full))


main()

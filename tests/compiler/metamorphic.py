#!/usr/bin/env python3
"""Metamorphic checks on the real engine: transformations of a document that
must not change behaviour (modulo the obvious renaming of outputs).

  python3 metamorphic.py --engine BIN [--docs N] [--steps N] [--seed S]
                         [--only T] [--expect-red]

Transformations (each applied to every random document):
  rename_nodes   bijective renaming of every node id (incl. unicode)  -> outputs renamed
  rename_names   events / conditions / calls / timer ids renamed       -> outputs renamed
  shuffle_children  order of `children` of every section               -> identical
  shuffle_on     order of `on`, `conditions`, `calls` dictionaries      -> identical
  annotations    x-… keys on doc/section/message/variant              -> identical
  unreachable    extra unreachable message+section with plain arrows   -> identical (hello.states +2)
  unreachable_resume  extra unreachable message with a resume arrow into an existing section
                 -> identical ACTIONS (snapshot `resume` may differ: decision 4 remembers more)
  numbers        delays written as 6e4 / 60000.0                        -> identical
  shuffle_timers order of a message's `timers` dictionary              -> docs/format.md (C3c) claims identical
                 behaviour; actions are compared exactly (expected to differ in order only)
Exit 1 on any violation of an invariant marked must-hold.
"""

import argparse
import json
import random
import sys

from engine import Engine
from gen import CALLS, CONDS, dumps, gen_model, to_json
from oracle import Bot, snap_from_json

MUST = ["rename_nodes", "rename_names", "shuffle_children", "shuffle_on", "annotations",
        "unreachable", "unreachable_resume", "numbers"]
CLAIMED = ["shuffle_timers"]


def walk_nodes(doc_bot, fn, name=""):
    fn(name, doc_bot)
    for k, v in doc_bot.get("children", {}).items():
        walk_nodes(v, fn, k)


def rename_nodes(doc, f):
    def rn(o):
        out = {}
        for k, v in o.items():
            if k == "initial":
                out[k] = f[v]
            elif k == "children":
                out[k] = {f[c]: rn(x) for c, x in v.items()}
            elif k == "on":
                out[k] = {e: [dict(vv, to=f[vv["to"]]) for vv in vs] for e, vs in v.items()}
            else:
                out[k] = v
        return out
    d = dict(doc)
    d["bot"] = rn(doc["bot"])
    return d


def rename_names(doc, fe, fc, fk, ft):
    def ev(e):
        return "timer:" + ft[e[6:]] if e.startswith("timer:") else fe.get(e, e)

    def var(v):
        v = dict(v)
        if "if" in v:
            v["if"] = fc[v["if"]]
        if "do" in v:
            v["do"] = [fk[x] for x in v["do"]]
        return v

    def rn(o):
        out = {}
        for k, v in o.items():
            if k == "children":
                out[k] = {c: rn(x) for c, x in v.items()}
            elif k == "on":
                out[k] = {ev(e): [var(x) for x in vs] for e, vs in v.items()}
            elif k == "timers":
                out[k] = {ft[t]: ms for t, ms in v.items()}
            else:
                out[k] = v
        return out
    d = dict(doc)
    d["conditions"] = {fc[k]: v for k, v in doc["conditions"].items()}
    d["calls"] = {fk[k]: v for k, v in doc["calls"].items()}
    d["bot"] = rn(doc["bot"])
    return d


def shuffled(rng, dct):
    items = list(dct.items())
    rng.shuffle(items)
    return dict(items)


def transform(rng, name, doc):
    """Returns (doc', out_map) where out_map maps base outputs to expected ones."""
    ident = {"node": {}, "event": {}, "cond": {}, "call": {}, "timer": {}}
    if name == "rename_nodes":
        names = []
        walk_nodes(doc["bot"], lambda n, o: names.append(n) if n else None)
        f = {n: f"узел-{i}·{n}" if i % 2 else f"N{i}" for i, n in enumerate(names)}
        m = dict(ident)
        m["node"] = f
        return rename_nodes(doc, f), m
    if name == "rename_names":
        timers = set()
        events = set()
        walk_nodes(doc["bot"], lambda n, o: (timers.update(o.get("timers", {})),
                                            events.update(o.get("on", {}))))
        fe = {e: "ev:" + e.upper() for e in events if not e.startswith("timer:")}
        fc = {c: "cond_" + c[::-1] for c in doc["conditions"]}
        fk = {c: "act." + c for c in doc["calls"]}
        ft = {t: "T" + t for t in timers}
        return rename_names(doc, fe, fc, fk, ft), {"node": {}, "event": fe, "cond": fc, "call": fk, "timer": ft}
    d = json.loads(json.dumps(doc))
    if name == "shuffle_children":
        def sh(o):
            if "children" in o:
                o["children"] = shuffled(rng, o["children"])
                for v in o["children"].values():
                    sh(v)
        sh(d["bot"])
    elif name == "shuffle_on":
        d["conditions"] = shuffled(rng, d["conditions"])
        d["calls"] = shuffled(rng, d["calls"])
        walk_nodes(d["bot"], lambda n, o: o.__setitem__("on", shuffled(rng, o["on"])) if "on" in o else None)
    elif name == "shuffle_timers":
        walk_nodes(d["bot"], lambda n, o: o.__setitem__("timers", dict(reversed(list(o["timers"].items()))))
                   if "timers" in o else None)
    elif name == "annotations":
        d["x-editor"] = {"v": 3, "nested": [1, {"a": None}]}
        d["x-top"] = True

        def ann(n, o):
            o["x-ui"] = {"x": 1.5, "y": -2e3}
            o["x-note"] = "a note"
            for vs in o.get("on", {}).values():
                for v in vs:
                    v["x-label"] = "a button"
        walk_nodes(d["bot"], ann)
    elif name == "unreachable":
        tgt = d["bot"]["initial"]
        d["bot"]["children"]["zz_orphan"] = {"on": {"btn:a": [{"to": tgt}], "btn:zz": [{"to": "zz_sec"}]}}
        d["bot"]["children"]["zz_sec"] = {"initial": "zz_in", "children": {"zz_in": {}}}
    elif name == "unreachable_resume":
        secs = []
        walk_nodes(d["bot"], lambda n, o: secs.append(n) if n and "children" in o else None)
        if not secs:
            return None, None
        d["bot"]["children"]["zz_orphan"] = {"on": {"btn:zz": [{"to": rng.choice(secs), "resume": True}]}}
    elif name == "numbers":
        def num(n, o):
            if "timers" in o:
                o["timers"] = {t: (float(ms) if i % 2 else ms) for i, (t, ms) in enumerate(o["timers"].items())}
        walk_nodes(d["bot"], num)
    return d, ident


def map_out(rep, m):
    """Rename names in a base engine reply according to map m."""
    fn = lambda n: m["node"].get(n, n)
    ft = lambda t: m["timer"].get(t, t)
    out = {}
    if "snapshot" in rep:
        s = rep["snapshot"]
        s2 = {"at": fn(s["at"])}
        if "back" in s:
            s2["back"] = fn(s["back"])
        if "resume" in s:
            s2["resume"] = {fn(k): fn(v) for k, v in s["resume"].items()}
        if "timers" in s:
            s2["timers"] = {ft(k): v for k, v in s["timers"].items()}
        if "seq" in s:
            s2["seq"] = s["seq"]
        out["snapshot"] = s2
        acts = []
        for a in rep["actions"]:
            if "send" in a:
                acts.append({"send": fn(a["send"])})
            elif "call" in a:
                acts.append({"call": m["call"].get(a["call"], a["call"])})
            elif "start_timer" in a:
                acts.append(dict(a, start_timer=ft(a["start_timer"])))
            else:
                acts.append({"cancel_timer": ft(a["cancel_timer"])})
        out["actions"] = acts
    for k in ("recovered", "need", "error"):
        if k in rep:
            out[k] = rep[k] if k != "need" else sorted(m["cond"].get(x, x) for x in rep[k])
    return out


def trace_inputs(rng, bot, steps):
    """Event sequence decided up front from the oracle's view of the document."""
    s, _ = bot.start()
    ins = []
    for _ in range(steps):
        at = s["at"]
        evs = sorted({e for n in [at] + bot.enclosing(at) for e, _ in bot.nodes[n].on})
        r = rng.random()
        if r < 0.1:
            ins.append(("btn:unknown", None))
        elif evs:
            e = rng.choice(evs)
            tok = None
            if e.startswith("timer:"):
                tok = s["timers"].get(e[6:], rng.randint(0, 5))
            ins.append((e, tok))
        else:
            ins.append(("btn:unknown", None))
        g = {c: rng.random() < 0.5 for c in CONDS}
        ins[-1] = ins[-1] + (g,)
        res = bot.step(s, ins[-1][0], ins[-1][1], g, check_need=False)
        s = res[1]
    return ins


def run(engine_bin, text, ins, m):
    e = Engine(engine_bin, text)
    outs = [{"hello_states": e.states}]
    rep = e.req({"op": "start"})
    outs.append(rep)
    snap = rep["snapshot"]
    for ev, tok, g in ins:
        if ev.startswith("timer:"):
            ev2 = "timer:" + m["timer"].get(ev[6:], ev[6:])
        else:
            ev2 = m["event"].get(ev, ev)
        req = {"op": "step", "snapshot": snap, "event": ev2,
               "guards": {m["cond"].get(k, k): v for k, v in g.items()}}
        if tok is not None:
            req["token"] = tok
        rep = e.req(req)
        rep.pop("id", None)
        outs.append(rep)
        if "snapshot" in rep:
            snap = rep["snapshot"]
    e.close()
    for o in outs:
        o.pop("id", None)
    return outs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", required=True)
    ap.add_argument("--docs", type=int, default=40)
    ap.add_argument("--steps", type=int, default=60)
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--only")
    ap.add_argument("--break-transform", help="deliberately break a transform's output mapping (red-proof)")
    ap.add_argument("--expect-red", action="store_true")
    args = ap.parse_args()
    names = [args.only] if args.only else MUST + CLAIMED
    res = {n: {"docs": 0, "steps": 0, "violations": 0, "example": None} for n in names}
    for k in range(args.docs):
        rng = random.Random(args.seed * 1000 + k)
        model = gen_model(rng, n_msgs=rng.choice([5, 9, 14]), n_secs=rng.choice([1, 3, 5]),
                          max_depth=rng.choice([2, 4]), p_hist=0.3, p_timer=0.5, p_ann=0.0)
        base = to_json(model)
        btext = dumps(base)
        bot = Bot(btext)
        ins = trace_inputs(rng, bot, args.steps)
        ident = {"node": {}, "event": {}, "cond": {}, "call": {}, "timer": {}}
        bout = run(args.engine, btext, ins, ident)
        for n in names:
            d2, m = transform(rng, n, base)
            if d2 is None:
                continue
            if args.break_transform == n:
                m = {"node": {}, "event": m["event"], "cond": m["cond"], "call": m["call"], "timer": m["timer"]} \
                    if m["node"] else dict(m, node={"__never__": "x"})
            t2 = dumps(d2)
            out2 = run(args.engine, t2, [(ev, tok, g) for ev, tok, g in ins], m)
            res[n]["docs"] += 1
            for i, (a, b) in enumerate(zip(bout, out2)):
                if i == 0:
                    exp = a["hello_states"] + (2 if n == "unreachable" else 1 if n == "unreachable_resume" else 0)
                    if b["hello_states"] != exp:
                        res[n]["violations"] += 1
                    continue
                res[n]["steps"] += 1
                ea = map_out(a, m)
                if n == "unreachable_resume":
                    ea.get("snapshot", {}).pop("resume", None)
                    b = json.loads(json.dumps(b))
                    b.get("snapshot", {}).pop("resume", None)
                if args.break_transform == n and i == 3 and "actions" in ea:
                    ea["actions"] = ea["actions"] + [{"send": "__broken__"}]
                if ea != b:
                    res[n]["violations"] += 1
                    if res[n]["example"] is None:
                        res[n]["example"] = {"doc_seed": k, "step": i, "expected": ea, "actual": b}
            # a diverged step makes later snapshots incomparable -> fine, counted per step
    bad = False
    for n in names:
        r = res[n]
        tag = "MUST" if n in MUST else "CLAIMED(C3c)"
        print(f"{n:20s} {tag:20s} docs={r['docs']} steps={r['steps']} violations={r['violations']}")
        if r["example"]:
            print("   example:", json.dumps(r["example"], ensure_ascii=False)[:600])
        if n in MUST and (r["violations"] or r["steps"] == 0):
            bad = True
    if args.expect_red:
        print("EXPECT-RED:", "RED (good)" if bad else "STILL GREEN")
        sys.exit(0 if bad else 1)
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Greedy shrinker for a divergence (document + one step from a snapshot).

  python3 shrink.py --engine BIN --div divergences.json [--index 0]
                    [--oracle-opt k=v ...] [--out repro.json]

A divergence is re-checked by running ONE step (snapshot, event, token,
guards) on the engine and on the oracle.  The shrinker deletes nodes, arrows,
variants, conditions on variants, calls, timers, annotations, snapshot
entries and guards while the divergence (same category) persists and the
document stays valid for both checkers."""

import argparse
import copy
import json
import random
import sys

from difflib_core import Divergence, do_step
from engine import Engine, EngineLoadError
from oracle import Bot, DocError, Options


def repair(d):
    """Remove arrows to missing nodes, fix initials, drop unused declarations."""
    names = set()

    def collect(o):
        for k, v in o.get("children", {}).items():
            names.add(k)
            collect(v)
    collect(d["bot"])
    timers = set()

    def tcol(o):
        timers.update(o.get("timers", {}))
        for v in o.get("children", {}).values():
            tcol(v)
    tcol(d["bot"])

    def fix(o):
        if "children" in o:
            if not o["children"]:
                return False
            if o["initial"] not in o["children"]:
                o["initial"] = next(iter(o["children"]))
            for k in list(o["children"]):
                if not fix(o["children"][k]):
                    del o["children"][k]
            if not o["children"]:
                return False
            if o["initial"] not in o["children"]:
                o["initial"] = next(iter(o["children"]))
        if "on" in o:
            for e in list(o["on"]):
                if e.startswith("timer:") and e[6:] not in timers:
                    del o["on"][e]
                    continue
                o["on"][e] = [v for v in o["on"][e] if v["to"] in names]
                if not o["on"][e]:
                    del o["on"][e]
            if not o["on"]:
                del o["on"]
        if "timers" in o and not o["timers"]:
            del o["timers"]
        return True
    for _ in range(3):
        fix(d["bot"])
        names.clear()
        collect(d["bot"])
    used_c, used_k = set(), set()

    def uses(o):
        for vs in o.get("on", {}).values():
            for v in vs:
                if "if" in v:
                    used_c.add(v["if"])
                used_k.update(v.get("do", []))
        for v in o.get("children", {}).values():
            uses(v)
    uses(d["bot"])
    if "conditions" in d:
        d["conditions"] = {k: v for k, v in d["conditions"].items() if k in used_c}
        if not d["conditions"]:
            del d["conditions"]
    if "calls" in d:
        d["calls"] = {k: v for k, v in d["calls"].items() if k in used_k}
        if not d["calls"]:
            del d["calls"]
    return d


def candidates(d, inp):
    """Yield (description, new_doc, new_input) one deletion at a time."""
    def paths(o, pre):
        for k in list(o.get("children", {})):
            yield ("child", pre + [k])
        for e in list(o.get("on", {})):
            yield ("event", pre + [e])
            for i, v in enumerate(o["on"][e]):
                yield ("variant", pre + [e, i])
                if "if" in v:
                    yield ("if", pre + [e, i])
                if "do" in v:
                    yield ("do", pre + [e, i])
                if "resume" in v:
                    yield ("resume", pre + [e, i])
                for k in [k for k in v if k not in ("if", "to", "resume", "do")]:
                    yield ("vann", pre + [e, i, k])
        for t in list(o.get("timers", {})):
            yield ("timer", pre + [t])
        for k in [k for k in o if k not in ("initial", "children", "on", "timers")]:
            yield ("nann", pre + [k])
        for k, v in o.get("children", {}).items():
            yield from paths(v, pre + [k])

    def node_at(dd, path):
        o = dd["bot"]
        for k in path:
            o = o["children"][k]
        return o

    for kind, p in list(paths(d["bot"], [])):
        dd = copy.deepcopy(d)
        try:
            if kind == "child":
                del node_at(dd, p[:-1])["children"][p[-1]]
            elif kind == "event":
                del node_at(dd, p[:-1])["on"][p[-1]]
            elif kind in ("variant", "if", "do", "resume", "vann"):
                q = p[:-3] if kind == "vann" else p[:-2]
                e, i = (p[-3], p[-2]) if kind == "vann" else (p[-2], p[-1])
                o = node_at(dd, q)
                if kind == "variant":
                    del o["on"][e][i]
                elif kind == "vann":
                    del o["on"][e][i][p[-1]]
                else:
                    del o["on"][e][i][{"if": "if", "do": "do", "resume": "resume"}[kind]]
            elif kind == "timer":
                del node_at(dd, p[:-1])["timers"][p[-1]]
            elif kind == "nann":
                del node_at(dd, p[:-1])[p[-1]]
        except (KeyError, IndexError):
            continue
        yield f"{kind} {p}", repair(dd), inp
    for k in [k for k in d if k.startswith("x-") or k not in
              ("figram", "requires", "conditions", "calls", "variables", "bot")]:
        dd = copy.deepcopy(d)
        del dd[k]
        yield f"doc key {k}", dd, inp
    s = inp["snapshot"]
    for part in ("resume", "timers"):
        for k in list(s.get(part, {})):
            i2 = copy.deepcopy(inp)
            del i2["snapshot"][part][k]
            if not i2["snapshot"][part]:
                del i2["snapshot"][part]
            yield f"snapshot {part}.{k}", d, i2
    if s.get("seq", 0) > 1 and inp.get("token") is None:
        i2 = copy.deepcopy(inp)
        i2["snapshot"]["seq"] = 1
        yield "snapshot seq=1", d, i2
    for g in list(inp.get("guards") or {}):
        i2 = copy.deepcopy(inp)
        del i2["guards"][g]
        yield f"guard {g}", d, i2


def diverges(engine, d, inp, opts, what):
    text = json.dumps(d, ensure_ascii=False)
    try:
        bot = Bot(text, opts)
    except DocError:
        return False
    try:
        eng = Engine(engine, text)
    except EngineLoadError:
        return False
    try:
        do_step(bot, eng, inp["snapshot"], inp["event"], inp["token"], random.Random(0), set(), {},
                fixed_guards=inp.get("guards") or {})
        return False
    except Divergence as dv:
        return dv.what == what
    finally:
        eng.close()


def shrink(engine, d, inp, opts, what):
    assert diverges(engine, d, inp, opts, what), "divergence does not reproduce"
    changed = True
    log = []
    while changed:
        changed = False
        for desc, d2, i2 in candidates(d, inp):
            if diverges(engine, d2, i2, opts, what):
                d, inp = d2, i2
                log.append(desc)
                changed = True
                break
    return d, inp, log


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", required=True)
    ap.add_argument("--div", required=True)
    ap.add_argument("--index", type=int, default=0)
    ap.add_argument("--oracle-opt", action="append", default=[])
    ap.add_argument("--out")
    args = ap.parse_args()
    opts = Options(**dict(kv.split("=", 1) for kv in args.oracle_opt))
    divs = json.load(open(args.div))
    dv = divs[args.index]
    d = json.loads(dv["doc"])
    inp = dv["ctx"]["input"]
    d2, i2, log = shrink(args.engine, d, inp, opts, dv["what"])
    res = {"what": dv["what"], "oracle_opts": args.oracle_opt, "doc": d2, "input": i2,
           "deletions": len(log),
           "doc_bytes_before": len(dv["doc"]), "doc_bytes_after": len(json.dumps(d2, ensure_ascii=False))}
    print(json.dumps(res, ensure_ascii=False, indent=1))
    if args.out:
        json.dump(res, open(args.out, "w"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()

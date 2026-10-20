#!/usr/bin/env python3
"""Acceptance differential: the oracle's own reading of FORMAT.md V1-V5 and
the grammar vs `figram-engine` load (decode + Doc.check + wf/namesOK).

  python3 check_diff.py --engine BIN [--docs N] [--seed S] [--expect-red] [--oracle-skip V]

For each random valid document one defect is injected (or none); the oracle
and the engine must agree on accept/reject."""

import argparse
import json
import random
import sys

from engine import engine_loads
from gen import dumps, gen_model, to_json
from oracle import Bot, DocError


def nodes_of(d):
    out = []

    def w(name, o, parent):
        out.append((name, o, parent))
        for k, v in o.get("children", {}).items():
            w(k, v, o)
    w("", d["bot"], None)
    return out


def inject(rng, d, kind):
    ns = nodes_of(d)
    msgs = [(n, o, p) for n, o, p in ns if "children" not in o]
    secs = [(n, o, p) for n, o, p in ns if "children" in o]
    arrows = [(n, o, e, v) for n, o, _ in ns for e, vs in o.get("on", {}).items() for v in vs]
    if kind == "none":
        return True
    if kind == "dup_node" and len(secs) >= 2:
        a, b = rng.sample(secs, 2)
        name = rng.choice([n for n, _, _ in msgs])
        b[1]["children"][name] = {}
        if name in a[1]["children"] and a is b:
            return False
        return True
    if kind == "initial_grandchild":
        for n, o, _ in secs:
            for k, v in o["children"].items():
                if "children" in v:
                    o["initial"] = v["initial"]
                    return True
        return False
    if kind == "initial_missing":
        rng.choice(secs)[1]["initial"] = "no_such"
        return True
    if kind == "to_missing" and arrows:
        rng.choice(arrows)[3]["to"] = "no_such"
        return True
    if kind == "resume_to_message" and arrows:
        v = rng.choice(arrows)[3]
        v["to"] = rng.choice(msgs)[0]
        v["resume"] = True
        return True
    if kind == "undeclared_if" and arrows:
        rng.choice(arrows)[3]["if"] = "undeclared"
        return True
    if kind == "undeclared_do" and arrows:
        rng.choice(arrows)[3]["do"] = ["undeclared"]
        return True
    if kind == "timer_event_no_timer":
        o = rng.choice(ns)[1]
        o.setdefault("on", {})["timer:nosuch"] = [{"to": msgs[0][0]}]
        return True
    if kind == "empty_event":
        o = rng.choice(ns)[1]
        o.setdefault("on", {})[""] = [{"to": msgs[0][0]}]
        return True
    if kind == "empty_timer":
        rng.choice(msgs)[1].setdefault("timers", {})[""] = 5
        return True
    if kind == "empty_if" and arrows:
        rng.choice(arrows)[3]["if"] = ""
        return True
    if kind == "empty_cond_key":
        d.setdefault("conditions", {})[""] = "x"
        return True
    if kind == "section_timers":
        rng.choice(secs)[1]["timers"] = {"t": 1}
        return True
    if kind == "message_initial":
        rng.choice(msgs)[1]["initial"] = "x"
        return True
    if kind == "requires":
        d["requires"] = ["section-timers"]
        return True
    if kind == "requires_empty":
        d["requires"] = []
        return True
    if kind == "big_delay":
        rng.choice(msgs)[1].setdefault("timers", {})["tb"] = 2 ** 53
        return True
    if kind == "max_delay":
        rng.choice(msgs)[1].setdefault("timers", {})["tm"] = 2 ** 53 - 1
        return True
    if kind == "frac_delay":
        rng.choice(msgs)[1].setdefault("timers", {})["tf"] = 1.5
        return True
    if kind == "resume_false_explicit" and arrows:
        rng.choice(arrows)[3]["resume"] = False
        return True
    if kind == "resume_not_bool" and arrows:
        rng.choice(arrows)[3]["resume"] = "yes"
        return True
    if kind == "unknown_key":
        rng.choice(ns)[1]["foo"] = {"bar": 1}
        return True
    if kind == "timer_named_like_event":
        m = rng.choice(msgs)
        m[1].setdefault("timers", {})["a:b"] = 3
        m[1].setdefault("on", {})["timer:a:b"] = [{"to": m[0]}]
        return True
    if kind == "node_named_bot":
        rng.choice(secs)[1]["children"]["bot"] = {}
        return True
    return False


KINDS = ["none", "dup_node", "initial_grandchild", "initial_missing", "to_missing",
         "resume_to_message", "undeclared_if", "undeclared_do", "timer_event_no_timer",
         "empty_event", "empty_timer", "empty_if", "empty_cond_key", "section_timers",
         "message_initial", "requires", "requires_empty", "big_delay", "max_delay", "frac_delay",
         "resume_false_explicit", "resume_not_bool", "unknown_key", "timer_named_like_event",
         "node_named_bot"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", required=True)
    ap.add_argument("--docs", type=int, default=10)
    ap.add_argument("--seed", type=int, default=3)
    ap.add_argument("--expect-red", action="store_true")
    ap.add_argument("--oracle-accept-all", action="store_true", help="broken oracle (red-proof)")
    args = ap.parse_args()
    counts, bad = {}, []
    for k in range(args.docs):
        for kind in KINDS:
            rng = random.Random(args.seed * 7919 + k * 101 + KINDS.index(kind))
            d = to_json(gen_model(rng, n_msgs=rng.choice([4, 8]), n_secs=rng.choice([2, 3]),
                                  max_depth=3, p_hist=0.3, p_timer=0.4))
            if not inject(rng, d, kind):
                continue
            text = dumps(d)
            try:
                Bot(text)
                o = True
            except DocError:
                o = False
            if args.oracle_accept_all:
                o = True
            e = engine_loads(args.engine, text)
            c = counts.setdefault(kind, [0, 0, 0])
            c[0] += 1
            c[1] += int(e)
            if o != e:
                c[2] += 1
                bad.append({"kind": kind, "oracle": o, "engine": e, "doc": text})
    total = sum(c[0] for c in counts.values())
    for kind, (n, acc, dis) in counts.items():
        print(f"{kind:24s} cases={n:3d} engine_accepts={acc:3d} disagreements={dis}")
    print(f"total cases {total}, disagreements {len(bad)}")
    for b in bad[:3]:
        print("DISAGREE", json.dumps(b, ensure_ascii=False)[:600])
    red = bool(bad)
    if args.expect_red:
        print("EXPECT-RED:", "RED (good)" if red else "STILL GREEN")
        sys.exit(0 if red else 1)
    sys.exit(1 if red or total == 0 else 0)


if __name__ == "__main__":
    main()

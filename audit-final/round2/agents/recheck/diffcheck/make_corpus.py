#!/usr/bin/env python3
"""Corpus for the old/new `figram check` differential (OldNewCheck.lean).

  python3 -I make_corpus.py REPO FIGRAM OUTDIR

Writes documents into OUTDIR and the list OUTDIR/list.txt:
  A. `figram random SEED N` (valid by construction);
  B. tests/compiler/gen.py models with each defect of check_diff.inject (valid and invalid);
  C. the same models with V5/V6 edge mutations: an arrow with no variants ([]), an empty `on`,
     root and section arrows on `call:` events (declared, undeclared, bad form), the same action
     with outcomes twice in `do` (latest, every, with `outcomes: []`, undeclared), names with `:`,
     timers of nested messages and `timer:` events on sections, an empty node key;
  D. hand-written edge documents.
"""
import json
import os
import random
import subprocess
import sys

REPO, FIGRAM, OUT = sys.argv[1], sys.argv[2], sys.argv[3]
sys.path.insert(0, os.path.join(REPO, "tests", "compiler"))
from gen import gen_model, to_json, dumps  # noqa: E402
import check_diff  # noqa: E402

os.makedirs(OUT, exist_ok=True)
paths = []


def put(name, doc):
    p = os.path.join(OUT, name + ".bot.json")
    with open(p, "w", encoding="utf-8") as f:
        f.write(doc if isinstance(doc, str) else json.dumps(doc, ensure_ascii=False))
    paths.append(p)


# A
for seed in range(1, 121):
    for n in (3, 8, 20):
        r = subprocess.run([FIGRAM, "random", str(seed), str(n)], capture_output=True, text=True, timeout=60)
        assert r.returncode == 0, r.stderr
        put(f"A_{seed}_{n}", r.stdout)


def nodes(d):
    out = []

    def w(name, o):
        out.append((name, o))
        for k, v in o.get("children", {}).items():
            w(k, v)
    w("", d["bot"])
    return out


def outcome_calls(d):
    return [k for k, v in d.get("calls", {}).items() if isinstance(v, dict) and v.get("outcomes")]


def edge(rng, d, kind):
    ns = nodes(d)
    msgs = [o for _, o in ns if "children" not in o]
    secs = [o for _, o in ns if "children" in o]
    names = [n for n, _ in ns if n]
    calls = d.setdefault("calls", {})
    oc = outcome_calls(d)
    pick = rng.choice
    if kind == "empty_variants":
        pick(ns)[1].setdefault("on", {})[f"btn:empty{rng.randrange(9)}"] = []
    elif kind == "empty_on":
        pick(ns)[1]["on"] = {}
    elif kind == "root_call_declared" and oc:
        n = pick(oc)
        o = pick(calls[n]["outcomes"] + ["failed"])
        d["bot"].setdefault("on", {})[f"call:{n}:{o}"] = [{"to": pick(names)}] if rng.random() < .5 else []
    elif kind == "section_call_undeclared_outcome" and oc:
        n = pick(oc)
        pick(secs).setdefault("on", {})[f"call:{n}:nosuch"] = [{"to": pick(names)}]
    elif kind == "call_bad_form":
        pick(ns)[1].setdefault("on", {})[pick(["call:x", "call::ok", "call:a:", "call:"])] = [{"to": pick(names)}]
    elif kind == "call_plain_action":
        calls["plainz"] = "plain"
        pick(ns)[1].setdefault("on", {})["call:plainz:failed"] = [{"to": pick(names)}]
    elif kind in ("twice_latest", "twice_every", "twice_empty_outcomes", "twice_every_empty", "twice_undeclared",
                  "twice_root", "twice_detached"):
        if kind == "twice_latest":
            calls["tw"] = {"text": "t", "outcomes": ["ok"]}
        elif kind == "twice_every":
            calls["tw"] = {"text": "t", "outcomes": ["ok"], "every": True}
        elif kind == "twice_empty_outcomes":
            calls["tw"] = {"text": "t", "outcomes": []}
        elif kind == "twice_every_empty":
            calls["tw"] = {"text": "t", "outcomes": [], "every": True}
        elif kind == "twice_detached":
            calls["tw"] = {"text": "t", "outcomes": ["ok"], "detached": True}
        elif kind == "twice_root":
            calls["tw"] = {"text": "t", "outcomes": ["ok"]}
        tgt = d["bot"] if kind == "twice_root" else pick(ns)[1]
        tgt.setdefault("on", {}).setdefault("btn:tw", []).insert(0, {"do": ["tw", "x" if False else "tw"]}
                                                                  if rng.random() < .5 else
                                                                  {"to": pick(names), "do": ["tw", "tw", "tw"]})
    elif kind == "colon_names" and True:
        calls["a:b"] = {"text": "t", "outcomes": ["ok"]}
        m = pick(msgs)
        m.setdefault("on", {})["btn:c"] = [{"do": ["a:b"]}]
        m["on"]["call:a:b:ok"] = [{"to": pick(names)}]
        m["on"]["call:a:b:failed"] = []
        if rng.random() < .5:
            m["on"]["call:a:b:c:ok"] = [{"to": pick(names)}]   # action `a:b:c` is not declared
    elif kind == "nested_timers":
        m = pick(msgs)
        m.setdefault("timers", {})["tz"] = 5
        s = pick(secs)
        s.setdefault("on", {})[pick(["timer:tz", "timer:nosuch"])] = [{"do": oc[:1]} if oc else {}]
    elif kind == "empty_key":
        s = pick(secs)
        s["children"][""] = {"on": {"btn:q": [{"do": ["tw2", "tw2"]}]}}
        calls["tw2"] = {"text": "t", "outcomes": ["ok"]}
    elif kind == "dup_call_decl_text":
        # a JSON text with a duplicate key in `calls`: the parser decides
        return "dupkey"
    else:
        return False
    return True


KINDS = ["empty_variants", "empty_on", "root_call_declared", "section_call_undeclared_outcome", "call_bad_form",
         "call_plain_action", "twice_latest", "twice_every", "twice_empty_outcomes", "twice_every_empty",
         "twice_undeclared", "twice_root", "twice_detached", "colon_names", "nested_timers", "empty_key"]

# B and C
k = 0
for i in range(150):
    for kind in check_diff.KINDS:
        rng = random.Random(1000 + i * 131 + check_diff.KINDS.index(kind))
        d = to_json(gen_model(rng, n_msgs=rng.choice([4, 8]), n_secs=rng.choice([2, 3]), max_depth=3,
                              p_hist=0.3, p_timer=0.4, p_feat=rng.choice([0.0, 0.7]), p_stay=rng.choice([0.0, 0.15])))
        if check_diff.inject(rng, d, kind):
            put(f"B_{i}_{kind}", d)
    for kind in KINDS:
        rng = random.Random(5000 + i * 131 + KINDS.index(kind))
        d = to_json(gen_model(rng, n_msgs=rng.choice([4, 8]), n_secs=rng.choice([2, 3]), max_depth=3,
                              p_hist=0.3, p_timer=0.4, p_feat=0.8, p_stay=rng.choice([0.0, 0.2])))
        # one or two edge mutations
        ok = edge(rng, d, kind)
        if ok and rng.random() < 0.4:
            edge(rng, d, rng.choice(KINDS))
        if ok is True:
            put(f"C_{i}_{kind}", d)

# D: hand-written
D = {
    "D_section_arrow_no_variants": {"figram": 1, "calls": {"c": {"text": "c", "outcomes": ["ok"]}},
        "bot": {"initial": "a", "on": {"call:c:ok": [], "call:c:nope": []},
                "children": {"a": {"on": {"btn:x": [{"do": ["c", "c"]}]}}}}},
    "D_root_twice": {"figram": 1, "calls": {"c": {"text": "c", "outcomes": ["ok"], "every": True}},
        "bot": {"initial": "a", "on": {"btn:r": [{"do": ["c", "c"]}, {"do": ["c"]}]},
                "children": {"a": {}}}},
    "D_colon_action": {"figram": 1, "calls": {"a:b": {"text": "c", "outcomes": ["ok"]}},
        "bot": {"initial": "a", "children": {"a": {"on": {"btn:x": [{"do": ["a:b", "a:b"]}],
                                                       "call:a:b:ok": [{"to": "a"}], "call:a:b:failed": []}}}}},
    "D_nested_timer_section_event": {"figram": 1, "bot": {"initial": "s", "on": {"timer:t": [{"to": "m"}]},
        "children": {"s": {"initial": "m", "children": {"m": {"timers": {"t": 5}}}}}}},
    "D_empty_on_everywhere": {"figram": 1, "bot": {"initial": "a", "on": {}, "children": {"a": {"on": {}}}}},
    "D_every_empty_twice": {"figram": 1, "calls": {"c": {"text": "c", "outcomes": [], "every": True}},
        "bot": {"initial": "a", "children": {"a": {"on": {"btn:x": [{"do": ["c", "c"]}]}}}}},
    "D_empty_key_twice": {"figram": 1, "calls": {"c": {"text": "c", "outcomes": ["ok"]}},
        "bot": {"initial": "", "children": {"": {"on": {"btn:x": [{"do": ["c", "c"]}]}}}}},
    "D_node_named_bot_twice": {"figram": 1, "calls": {"c": {"text": "c", "outcomes": ["ok"]}},
        "bot": {"initial": "bot", "children": {"bot": {"on": {"btn:x": [{"do": ["c", "c"]}]}}}}},
}
for name, doc in D.items():
    put(name, doc)

with open(os.path.join(OUT, "list.txt"), "w") as f:
    f.write("\n".join(paths) + "\n")
print(f"documents {len(paths)}")

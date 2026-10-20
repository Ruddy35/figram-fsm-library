#!/usr/bin/env python3
"""Red-proof of the differential driver: every engine mutant that
`figram-engine --mutants` offers for a random document must be detected by
the same random trace the real engine passes.

  python3 mutants_red.py --engine BIN [--docs N] [--steps N] [--seed S]

Prints kill statistics; exit 0 iff the real engine is green on every doc
used AND every surviving mutant is classified as equivalent (a dead variant
that no guard values can select, or a reversed row that behaves identically
for all guard values)."""

import argparse
import json
import subprocess
import sys
import tempfile

import diff_random
from gen import dumps, to_json


def preorder(bot):
    """Node names in document pre-order (section, then its children)."""
    kids = {}
    for n, x in bot.nodes.items():
        if x.parent is not None:
            kids.setdefault(x.parent, []).append(n)
    out = []

    def walk(n):
        out.append(n)
        for k in kids.get(n, []):
            walk(k)
    walk(bot.ROOT)
    return out


def compiled_rows(bot):
    """Row order of the compiled automaton (for classifying survivors only):
    messages in pre-order x events ordered by LAST occurrence, non-empty rows."""
    order = preorder(bot)
    evs = [e for n in order for e, _ in bot.nodes[n].on]
    last = []
    for i, e in enumerate(evs):
        if e not in evs[i + 1:]:
            last.append(e)
    rows = []
    for L in order:
        if not bot.is_message(L):
            continue
        for e in last:
            c = bot.candidates(L, e)
            if c:
                rows.append((L, e, c))
    return rows


BOT = None


def outcome(cands, g):
    """Observable result of the first true variant: landed target (an arrow
    into a section lands on its initial message), resume flag, calls."""
    for v in cands:
        if v.cond is None or g.get(v.cond):
            to = v.to if (v.resume or BOT is None) else BOT.landing(v.to)
            return (to, v.resume, tuple(v.calls))
    return None


def classify(bot, mutant):
    global BOT
    BOT = bot
    from exhaustive import select_guards
    import itertools
    rows = compiled_rows(bot)
    flat = [(r, i) for r in rows for i in range(len(r[2]))]
    kind, _, k = mutant.partition(":")
    if kind in ("redirect", "drop-guard", "reverse-actions"):
        (L, e, c), i = flat[int(k)]
        if select_guards(c, i) is None:
            return "equivalent: dead variant %d of %s/%s" % (i, L, e)
        if kind == "drop-guard":
            from oracle import Variant
            mc = list(c)
            mc[i] = Variant(None, c[i].to, c[i].resume, c[i].calls)
            names = sorted({v.cond for v in c if v.cond})
            for bits in itertools.product([False, True], repeat=len(names)):
                g = dict(zip(names, bits))
                if outcome(c, g) != outcome(mc, g):
                    return "LIVE variant %d of %s/%s" % (i, L, e)
            return "equivalent: dropping the guard selects an identical later variant"
        return "LIVE variant %d of %s/%s" % (i, L, e)
    if kind == "swap-branches":
        L, e, c = rows[int(k)]
        names = sorted({v.cond for v in c if v.cond})
        rc = list(reversed(c))
        for bits in itertools.product([False, True], repeat=len(names)):
            g = dict(zip(names, bits))
            if outcome(c, g) != outcome(rc, g):
                return "LIVE row %s/%s" % (L, e)
        return "equivalent: reversed row behaves identically for all guard values"
    return "unclassified"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", required=True)
    ap.add_argument("--docs", type=int, default=10)
    ap.add_argument("--steps", type=int, default=150)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--max-mutants", type=int, default=100000)
    ap.add_argument("--out")
    ap.add_argument("--oracle-opt", action="append", default=[],
                    help="k=v ambiguity switch of oracle.Options, passed to diff_random")
    args = ap.parse_args()
    killed = total = 0
    survivors = []
    for k in range(args.docs):
        seed = args.seed * 100003 + k
        _, model = diff_random.make_model(seed)
        text = dumps(to_json(model))
        with tempfile.NamedTemporaryFile("w", suffix=".bot.json", delete=False) as f:
            f.write(text)
        muts = subprocess.run([args.engine, "--mutants", f.name], capture_output=True, text=True,
                              check=True, timeout=60).stdout.split()
        base = argparse.Namespace(engine=args.engine, steps=args.steps, mutate=None,
                                  oracle_bug=None, no_upgrade=True, oracle_opt=args.oracle_opt)
        st, divs = {}, []
        diff_random.one_doc(base, seed, st, set(), divs)
        if divs:
            print("REAL ENGINE DIVERGES on seed", seed, divs[0]["what"])
            sys.exit(1)
        for m in muts[: args.max_mutants]:
            a = argparse.Namespace(engine=args.engine, steps=args.steps, mutate=m,
                                   oracle_bug=None, no_upgrade=True, oracle_opt=args.oracle_opt)
            st2, d2 = {}, []
            diff_random.one_doc(a, seed, st2, set(), d2)
            total += 1
            if d2:
                killed += 1
            else:
                from oracle import Bot
                why = classify(Bot(text), m)
                survivors.append({"seed": seed, "mutant": m, "why": why, "doc": text})
    equiv = [s for s in survivors if s["why"].startswith("equivalent")]
    live = [s for s in survivors if not s["why"].startswith("equivalent")]
    print(json.dumps({"mutants": total, "killed": killed, "survivors": len(survivors),
                      "equivalent_survivors": len(equiv), "non_equivalent_survivors": len(live),
                      "kill_rate_non_equivalent": round(killed / max(1, total - len(equiv)), 4)}))
    for s in live:
        print("SURVIVOR", s["seed"], s["mutant"], s["why"])
    if args.out:
        with open(args.out, "w") as f:
            json.dump(survivors, f, ensure_ascii=False, indent=1)
    sys.exit(0 if total and not live else 1)


if __name__ == "__main__":
    main()

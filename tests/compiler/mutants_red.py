#!/usr/bin/env python3
"""Red-proof of the differential driver: every engine mutant that
`figram-engine --mutants` offers for a random document must be detected by
the same random trace the real engine passes.

  python3 mutants_red.py --engine BIN [--docs N] [--steps N] [--seed S]

Prints kill statistics; exit 0 iff the real engine is green on every doc
used AND every surviving mutant is classified as equivalent (a dead variant
that no guard values can select, or a reversed row that behaves identically
for all guard values; with --oracle-opt timer_accept=owned (CC-01 fixed) also a
`timer:X` row of a message that does not own X, `reverse-entry` of a message no
step enters, and `drop-exit` when no message has two timers and no timer arrow
is on its owner).

  python3 mutants_red.py --reclassify SURVIVORS.json [--oracle-opt ...]
re-classifies the survivors saved by --out without running the engine."""

import argparse
import os
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
# CC01=fixed (oracle option timer_accept=owned): a timer event is accepted only on the
# message that owns the timer, so rows `timer:X` of other messages are dead.
OWNED = False


def owned_ok(bot, L, e):
    """False iff the row (L, e) can never be taken: `timer:X` on a message without X."""
    return not (OWNED and e.startswith("timer:") and not any(t == e[6:] for t, _ in bot.nodes[L].timers))


def oracle_ignores(text, L, e):
    """The designer oracle (CC-01 reading) ignores `timer:X` on message L even with the
    current token: the row (L, timer:X) is never taken (cross-check of owned_ok)."""
    from oracle import Bot, Options
    b = Bot(text, Options(timer_accept="owned", exit_cancels="snapshot"))
    snap = {"at": L, "resume": {}, "timers": {e[6:]: 5}, "seq": 5}
    r = b.step(snap, e, 5, {}, check_need=False)
    return r[0] == "ok" and r[1] == snap and r[2] == [] and not r[3]


def dead_row(bot, L, e, text):
    if owned_ok(bot, L, e):
        return None
    if text is not None and not oracle_ignores(text, L, e):
        return "LIVE timer row %s/%s: the oracle accepts the timer on a non-owner" % (L, e)
    return "equivalent: timer arrow %s/%s on a message that does not own the timer" % (L, e)


def entered(bot, rows):
    """Messages that some step can enter: the start/recovery landing and the landing of
    every live variant (a resume variant may land on any message of its section)."""
    from exhaustive import select_guards
    out = {bot.landing(bot.ROOT)}
    for L, e, c in rows:
        if not owned_ok(bot, L, e):
            continue
        for i, v in enumerate(c):
            if select_guards(c, i) is None:
                continue
            if v.resume:
                out.add(bot.landing(v.to))
                out |= {m for m in bot.nodes if bot.is_message(m) and m != v.to and v.to in bot.enclosing(m)}
            else:
                out.add(bot.landing(v.to))
    return out


def outcome(cands, g):
    """Observable result of the first true variant: landed target (an arrow
    into a section lands on its initial message), resume flag, calls."""
    for v in cands:
        if v.cond is None or g.get(v.cond):
            to = v.to if (v.resume or BOT is None) else BOT.landing(v.to)
            return (to, v.resume, tuple(v.calls))
    return None


def classify(bot, mutant, text=None):
    global BOT
    BOT = bot
    from exhaustive import select_guards
    import itertools
    rows = compiled_rows(bot)
    flat = [(r, i) for r in rows for i in range(len(r[2]))]
    kind, _, k = mutant.partition(":")
    if kind in ("redirect", "drop-guard", "reverse-actions"):
        (L, e, c), i = flat[int(k)]
        why = dead_row(bot, L, e, text)
        if why:
            return why
        if select_guards(c, i) is None:
            return "equivalent: dead variant %d of %s/%s" % (i, L, e)
        if kind == "drop-guard":
            from oracle import Variant
            mc = list(c)
            mc[i] = Variant(None, c[i].to, c[i].resume, c[i].calls, c[i].back)
            names = sorted({v.cond for v in c if v.cond})
            for bits in itertools.product([False, True], repeat=len(names)):
                g = dict(zip(names, bits))
                if outcome(c, g) != outcome(mc, g):
                    return "LIVE variant %d of %s/%s" % (i, L, e)
            return "equivalent: dropping the guard selects an identical later variant"
        return "LIVE variant %d of %s/%s" % (i, L, e)
    if kind == "swap-branches":
        L, e, c = rows[int(k)]
        why = dead_row(bot, L, e, text)
        if why:
            return why
        names = sorted({v.cond for v in c if v.cond})
        rc = list(reversed(c))
        for bits in itertools.product([False, True], repeat=len(names)):
            g = dict(zip(names, bits))
            if outcome(c, g) != outcome(rc, g):
                return "LIVE row %s/%s" % (L, e)
        return "equivalent: reversed row behaves identically for all guard values"
    msgs = [n for n in preorder(bot) if bot.is_message(n)]
    if kind == "reverse-entry":
        L = msgs[int(k)]
        if L not in entered(bot, rows):
            return "equivalent: message %s is never entered (entry actions never run)" % L
        return "LIVE entry of %s" % L
    if kind == "drop-exit" and OWNED:
        # with CC-01 fixed leaving a message cancels every snapshot timer anyway (staleCancels);
        # dropping `exit` is visible only through the cancel order of a message with >= 2 timers
        # or through an owned timer arrow (ownsTimer reads exit)
        multi = [n for n in msgs if len(bot.nodes[n].timers) >= 2]
        owned_rows = [(L, e) for L, e, _ in rows if e.startswith("timer:") and owned_ok(bot, L, e)]
        if not multi and not owned_rows:
            return "equivalent: every message has at most one timer and no timer arrow is on its owner"
        return "LIVE exit (timers %s, owned timer arrows %s)" % (multi, owned_rows)
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
    ap.add_argument("--reclassify", help="survivors JSON of a previous --out run")
    args = ap.parse_args()
    global OWNED
    OWNED = "timer_accept=owned" in args.oracle_opt
    if args.reclassify:
        from oracle import Bot
        sv = json.load(open(args.reclassify, encoding="utf-8"))
        live = 0
        for x in sv:
            why = classify(Bot(x["doc"]), x["mutant"], x["doc"])
            if not why.startswith("equivalent"):
                live += 1
                print("SURVIVOR", x["seed"], x["mutant"], why)
        print(json.dumps({"survivors": len(sv), "non_equivalent_survivors": live}))
        sys.exit(0 if sv and not live else 1)
    killed = total = 0
    survivors = []
    for k in range(args.docs):
        seed = args.seed * 100003 + k
        _, model = diff_random.make_model(seed)
        text = dumps(to_json(model))
        with tempfile.NamedTemporaryFile("w", suffix=".bot.json", delete=False) as f:
            f.write(text)
        muts = subprocess.run([args.engine, "--mutants", f.name], capture_output=True, text=True,
                              check=True, timeout=60,
                              env=dict(os.environ, FIGRAM_ALLOW_MUTANTS="1")).stdout.split()
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
                why = classify(Bot(text), m, text)
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

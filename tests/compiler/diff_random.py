#!/usr/bin/env python3
"""Differential test: independent oracle vs figram-engine on random documents,
random event sequences (stale/forged timer tokens; outcome events with the token of a wait
of `pending`, a stale or forged token, an undeclared outcome), stale snapshots (forged,
duplicated and foreign waits) and document upgrades v1 -> v2 (docs/host-contract.md,
section 6; an action removed, losing its outcomes or `every`).  Every reply is compared
with the oracle byte for byte (difflib_core.check_bytes).

  python3 diff_random.py --engine BIN [--docs N] [--steps N] [--seed S]
                         [--mutate M] [--oracle-bug B] [--expect-red]
                         [--out DIR]   (writes divergences as JSON)

Exit 0: no divergence and the case counts are non-zero.  Exit 1: divergence
(or zero cases).  With --expect-red the meaning is inverted: exit 0 only if a
divergence WAS found (used to prove the test can go red).
"""

import argparse
import json
import os
import random
import sys
import time

import exhaustive
from difflib_core import Divergence, do_restart, do_start, do_start_with, do_step
from engine import Engine, EngineLoadError
from gen import dumps, gen_model, perturb_snapshot, pick_event, to_json, upgrade
from oracle import Bot, DocError, Options, snap_from_json


def make_model(seed):
    rng = random.Random(seed)
    size = rng.choice([(4, 1, 2), (8, 3, 3), (12, 4, 4), (20, 8, 6), (6, 5, 5)])
    # background actions, actions with outcomes (latest / every) and arrows without a target
    # (docs/format.md, section 7, decisions 11-13) in most documents, none in some
    model = gen_model(rng, n_msgs=size[0], n_secs=size[1], max_depth=size[2],
                      p_hist=rng.choice([0.0, 0.2, 0.4]), p_timer=rng.choice([0.2, 0.5]),
                      p_feat=rng.choice([0.0, 0.5, 0.8, 1.0]), p_stay=rng.choice([0.0, 0.12, 0.25]))
    return rng, model


def one_doc(args, seed, stats, hits, divs):
    rng, model = make_model(seed)
    text = dumps(to_json(model))
    kw = dict(kv.split("=", 1) for kv in (getattr(args, "oracle_opt", None) or []))
    opts = Options(bug=args.oracle_bug, **kw)
    try:
        bot = Bot(text, opts)
    except DocError as e:
        # the generator keeps documents valid (V1-V6); a document the oracle rejects must be
        # rejected by the engine too (acceptance is the subject of check_diff.py)
        stats["gen_invalid"] = stats.get("gen_invalid", 0) + 1
        try:
            Engine(args.engine, text, mutate=args.mutate).close()
            divs.append({"seed": seed, "what": "engine accepted a doc the oracle rejects", "doc": text,
                         "oracle": e.errors})
        except EngineLoadError:
            pass
        return
    try:
        eng = Engine(args.engine, text, mutate=args.mutate)
    except EngineLoadError as e:
        divs.append({"seed": seed, "what": "engine rejected a doc the oracle accepts",
                     "doc": text, "err": str(e)})
        return
    stats["docs"] = stats.get("docs", 0) + 1
    if eng.states != bot.message_count():
        divs.append({"seed": seed, "what": "hello.states differs", "doc": text,
                     "engine": eng.states, "oracle": bot.message_count()})
    cur_text = text
    try:
        if not getattr(args, "no_exhaustive", False):
            for snap_j, ev, tok, g, tag in exhaustive.cases(bot, rng):
                stats.setdefault("exhaustive", {})
                stats["exhaustive"][tag[0]] = stats["exhaustive"].get(tag[0], 0) + 1
                if snap_j is None:
                    continue
                h = set()
                do_step(bot, eng, snap_j, ev, tok, rng, h, stats, fixed_guards=g)
                for x in h:
                    stats.setdefault("hits_steps", {})
                    stats["hits_steps"][x + "@exhaustive"] = stats["hits_steps"].get(x + "@exhaustive", 0) + 1
                hits |= h
            for op, ev, tok, g, x, tag in exhaustive.initial_cases(bot, rng):
                stats.setdefault("initial_cases", {})
                key = "-".join(tag)
                stats["initial_cases"][key] = stats["initial_cases"].get(key, 0) + 1
                h = set()
                if op == "start":
                    do_start_with(bot, eng, ev, rng, stats, seq=x, hits=h, fixed_guards=g)
                else:
                    do_step(bot, eng, x, ev, tok, rng, h, stats, fixed_guards=g)
                for y in h:
                    stats.setdefault("hits_steps", {})
                    stats["hits_steps"][y + "@initial"] = stats["hits_steps"].get(y + "@initial", 0) + 1
                hits |= h
        if rng.random() < 0.5:   # a new user with a first event (format.md §7, decision 9)
            first = {"at": bot.landing(bot.ROOT), "resume": {}, "timers": {}, "seq": 0, "back": None,
                     "pending": []}
            ev, token, _ = pick_event(rng, bot, first, {})
            if token is not None and ev.startswith("call:") and rng.random() < 0.5:
                do_start_with(bot, eng, ev, rng, stats)      # rejected: no outcome starts a user
            h = set()
            snap = do_start_with(bot, eng, ev if token is None else "btn:zz", rng, stats,
                                 seq=rng.choice([0, 0, 41]), hits=h)
            for x in h:
                stats.setdefault("hits_steps", {})
                stats["hits_steps"][x + "@start"] = stats["hits_steps"].get(x + "@start", 0) + 1
            hits |= h
        else:
            snap = do_start(bot, eng, stats)
        older = [snap_from_json(snap)]
        tokens = {}
        upgraded = False
        perturbed = False
        for i in range(args.steps):
            if not getattr(args, "no_upgrade", False) and not upgraded and i == args.steps // 2 and rng.random() < 0.6:
                s = snap_from_json(snap)
                focus = [s["at"]] + list(s["resume"].values()) + list(s["resume"].keys()) + \
                    ([s["back"]] if s.get("back") else [])
                for _ in range(6):
                    m2, desc = upgrade(rng, model, focus=focus)
                    if m2:
                        break
                if m2:
                    eng.close()
                    model = m2
                    cur_text = dumps(to_json(model))
                    bot = Bot(cur_text, opts)
                    eng = Engine(args.engine, cur_text, mutate=args.mutate)
                    upgraded = True
                    stats["upgrades"] = stats.get("upgrades", 0) + 1
                    stats.setdefault("upgrade_ops", {})
                    k = desc.split()[0]
                    stats["upgrade_ops"][k] = stats["upgrade_ops"].get(k, 0) + 1
            r = rng.random()
            if r < 0.06 and not getattr(args, "no_perturb", False):
                s2, kind = perturb_snapshot(rng, bot, snap_from_json(snap), older, None)
                from oracle import snap_to_json
                snap = snap_to_json(s2)
                perturbed = True
                stats.setdefault("perturb", {})
                stats["perturb"][kind] = stats["perturb"].get(kind, 0) + 1
            if r > 0.985:
                snap = do_restart(bot, eng, snap, stats)
                continue
            ev, token, kind = pick_event(rng, bot, snap_from_json(snap), tokens)
            stats.setdefault("events", {})
            stats["events"][kind] = stats["events"].get(kind, 0) + 1
            h = set()
            snap, rec = do_step(bot, eng, snap, ev, token, rng, h, stats)
            for x in h:
                key = x + ("@upgraded" if upgraded else "") + ("@perturbed" if perturbed else "")
                stats.setdefault("hits_steps", {})
                stats["hits_steps"][key] = stats["hits_steps"].get(key, 0) + 1
            hits |= h
            if "error" in rec.get("reply", {}):
                snap = do_start(bot, eng, stats)
                perturbed = False
            s = snap_from_json(snap)
            for t, tok in list(s["timers"].items()) + [("call:" + w["call"], w["token"]) for w in s["pending"]]:
                tokens.setdefault(t, [])
                if tok not in tokens[t]:
                    tokens[t].append(tok)
            older.append(s)
            if len(older) > 20:
                older.pop(0)
            stats["steps"] = stats.get("steps", 0) + 1
    except Divergence as d:
        divs.append({"seed": seed, "what": d.what, "doc": cur_text,
                     "ctx": json.loads(json.dumps(d.ctx, default=str))})
    finally:
        eng.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", required=True)
    ap.add_argument("--docs", type=int, default=100)
    ap.add_argument("--steps", type=int, default=60)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--mutate")
    ap.add_argument("--oracle-bug")
    ap.add_argument("--expect-red", action="store_true")
    ap.add_argument("--out")
    ap.add_argument("--stop-at-first", action="store_true")
    ap.add_argument("--no-upgrade", action="store_true")
    ap.add_argument("--no-exhaustive", action="store_true")
    ap.add_argument("--no-perturb", action="store_true")
    ap.add_argument("--oracle-opt", action="append", default=[],
                    help="k=v ambiguity switch of oracle.Options (e.g. timer_accept=owned)")
    args = ap.parse_args()
    stats, hits, divs = {}, set(), []
    hit_counts = {}
    t0 = time.time()
    for k in range(args.docs):
        h = set()
        one_doc(args, args.seed * 100003 + k, stats, h, divs)
        for x in h:
            hit_counts[x] = hit_counts.get(x, 0) + 1
        if divs and (args.stop_at_first or args.expect_red):
            break
    dt = time.time() - t0
    print(json.dumps({"stats": stats, "ambiguity_hits_docs": hit_counts,
                      "divergences": len(divs), "seconds": round(dt, 1)}, ensure_ascii=False))
    for d in divs[:5]:
        print("DIVERGENCE", json.dumps(d, ensure_ascii=False)[:2000])
    if args.out:
        os.makedirs(args.out, exist_ok=True)
        with open(os.path.join(args.out, "divergences.json"), "w") as f:
            json.dump(divs, f, ensure_ascii=False, indent=1)
    if args.expect_red:
        ok = bool(divs)
        print("EXPECT-RED:", "RED (good)" if ok else "STILL GREEN (test too weak)")
        sys.exit(0 if ok else 1)
    if divs or stats.get("steps", 0) == 0 or stats.get("docs", 0) == 0:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()

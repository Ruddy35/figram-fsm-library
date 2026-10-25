#!/usr/bin/env python3
"""(a) Round-trip of documents through the text layer.

For every base document (``figram random <seed> <n>`` for many seeds, plus all
of examples/*.bot.json) we build non-canonical variants of the SAME document:

  V0  the base text as is
  V1  compact, ensure_ascii (every non-ASCII char as \\uXXXX, surrogate pairs)
  V2  known keys shuffled inside fixed-key objects, explicit defaults
      (resume:false, do:[], on:{}, timers:{}, requires:[], variables:{}),
      timer delays rewritten as 7.2e3 / 7200.0 / 72e2 / 7200000e-3 / -0
  V3  annotations: x-… keys with small random JSON values
      (unicode, escapes, control chars, numbers with fractions/exponents,
      nested arrays/objects, empty containers) in doc/section/message/variant
  V4  V2 + V3 + random whitespace (space, tab, CR, LF)
  V5  V3 with unknown keys without the x- prefix as well (at least one)

and check, for every variant x:

  P1  `figram fmt x`  ==  oracle(x)   (independent Python decode∘encode∘print,
      tests/formats/common.py; numbers kept as exact m·10^e)
  P2  `figram fmt (figram fmt x)` == `figram fmt x`   (idempotence, i.e. the
      printed text is read back by Figram itself to the same document)
  P3  python json.loads(fmt x, object_pairs_hook) == oracle model (structure)
  P4  `figram check x` prints ok, `figram stats x` and
      `figram trace x 3 8 <seed>` are byte-identical to the base document
      (C5a: annotations / defaults / number spelling do not change behaviour);
      for V5, `figram check x` and `figram stats x` reject the document with
      "unknown key" (docs/format.md, section 5)

Rows of a fixed defect (FC-01) are run separately (see EDGE below); they
FAIL on the baseline (commit d69bbaf); use --strict to make them count.

    python3 a_roundtrip.py [--seeds N] [--break ORACLE_BUG] [--strict]
"""

import argparse
import glob
import json
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import (FIGRAM, ROOT, Canon, Num, Obj, RawNum, load_j, oracle_fmt, render,  # noqa: E402
                    run, write)

DEFAULT_KEYS = {
    "doc": [("requires", []), ("variables", Obj()), ("conditions", Obj()), ("calls", Obj())],
    "msg": [("on", Obj()), ("timers", Obj())],
    "sec": [("on", Obj())],
    "var": [("resume", False), ("back", False), ("do", [])],
}


def rand_str(r):
    alphabet = ["a", "Z", " ", "é", "Ж", "😀", " ", "\x00", "\x1b", "\n", "\t", '"', "\\", "/",
                "\x7f", "﻿", "x-", " "]
    return "".join(r.choice(alphabet) for _ in range(r.randint(0, 6)))


def rand_num(r):
    k = r.random()
    if k < 0.3:
        return RawNum(str(r.randint(-10 ** 6, 10 ** 6)))
    if k < 0.5:
        return RawNum(r.choice(["0", "-0", "0.0", "-0.0", "1.50", "100", "1E+2", "1e2", "15e2", "1.5e3",
                                "0.001", "123.456e-2", "9007199254740993", "-1e-31", "2.5E-40"]))
    sign = r.choice(["", "-"])
    ip = r.choice(["0", str(r.randint(1, 999))])
    fp = "." + "".join(r.choice("0123456789") for _ in range(r.randint(1, 8))) if r.random() < 0.6 else ""
    ex = (r.choice("eE") + r.choice(["", "+", "-"]) + str(r.randint(0, 300))) if r.random() < 0.5 else ""
    return RawNum(sign + ip + fp + ex)


def rand_value(r, depth=0):
    k = r.random()
    if depth >= 3 or k < 0.5:
        c = r.random()
        if c < 0.3:
            return rand_num(r)
        if c < 0.7:
            return rand_str(r)
        return r.choice([None, True, False])
    if k < 0.75:
        return [rand_value(r, depth + 1) for _ in range(r.randint(0, 3))]
    keys = []
    for _ in range(r.randint(0, 3)):
        kk = rand_str(r)
        if kk not in keys:
            keys.append(kk)
    return Obj((kk, rand_value(r, depth + 1)) for kk in keys)


ANN_KEYS = ["x-ui", "x-note", "x-content", "x-", "x-ui2"]
UNKNOWN_KEYS = ["note", "Bot", "TO", "initial2", "zz", "_id", "X-ui", "x_ui"]


def ann_key(r, known, plain):
    """An annotation key not in `known`; with `plain`, unknown keys without x- too."""
    while True:
        k = r.choice(ANN_KEYS + ["x-" + rand_str(r)] + (UNKNOWN_KEYS + [rand_str(r)] if plain else []))
        if k not in known:
            return k


def insert_at(r, obj, pair):
    obj.insert(r.randint(0, len(obj)), pair)


def transform(j, r, shuffle, defaults, annotate, numbers, plain=False):
    """Walk the document J and produce a variant of the same document."""

    def var(v):
        v = Obj(v)
        if defaults:
            for k, d in DEFAULT_KEYS["var"]:
                if not v.has(k) and r.random() < 0.5:
                    insert_at(r, v, (k, d))
        if annotate:
            for _ in range(r.randint(0, 2)):
                k = ann_key(r, [kk for kk, _ in v] + ["if", "to", "resume", "back", "do"], plain)
                insert_at(r, v, (k, rand_value(r)))
        if shuffle:
            v = shuffle_known(v, ["if", "to", "resume", "back", "do"])
        return v

    def on(o):
        return Obj((ev, [var(x) for x in vs]) for ev, vs in o)

    def node(n):
        n = Obj(n)
        is_sec = n.has("children")
        out = Obj()
        for k, v in n:
            if k == "on":
                out.append((k, on(v)))
            elif k == "children":
                out.append((k, Obj((kk, node(x)) for kk, x in v)))
            elif k == "timers" and numbers:
                out.append((k, Obj((kk, respell(x)) for kk, x in v)))
            else:
                out.append((k, v))
        if defaults:
            for k, d in DEFAULT_KEYS["sec" if is_sec else "msg"]:
                if not out.has(k) and r.random() < 0.5:
                    insert_at(r, out, (k, d))
        if annotate:
            for _ in range(r.randint(0, 2)):
                k = ann_key(r, [kk for kk, _ in out] + ["initial", "children", "on", "timers"], plain)
                insert_at(r, out, (k, rand_value(r)))
        if shuffle:
            out = shuffle_known(out, ["initial", "children", "on", "timers"])
        return out

    def respell(x):
        assert isinstance(x, Num) and x.e == 0 and x.m >= 0
        n = x.m
        forms = [str(n), f"{n}.0", f"{n}.000", f"{n}e0", f"{n}E+0", f"{n * 1000}e-3", f"{n}0e-1"]
        if n == 0:
            forms += ["-0", "-0.0", "0e5", "0.0e-0"]
        else:
            s = str(n)
            forms.append(f"{s[0]}.{s[1:] or '0'}e{len(s) - 1}")
        return RawNum(r.choice(forms))

    def shuffle_known(o, known):
        """Permute the positions of known keys only; unknown keys keep their
        relative order (C1: unknown key order is part of the document)."""
        idx = [i for i, (k, _) in enumerate(o) if k in known]
        vals = [o[i] for i in idx]
        r.shuffle(vals)
        o = Obj(o)
        for i, p in zip(idx, vals):
            o[i] = p
        return o

    d = Obj(j)
    out = Obj()
    for k, v in d:
        if k == "bot":
            out.append((k, node(v)))
        elif k == "figram" and numbers:
            out.append((k, RawNum(r.choice(["1", "1.0", "1e0", "10e-1", "0.1e1", "1.000", "100E-2"]))))
        else:
            out.append((k, v))
    if defaults:
        for k, dflt in DEFAULT_KEYS["doc"]:
            if not out.has(k) and r.random() < 0.5:
                insert_at(r, out, (k, dflt))
    if annotate:
        for _ in range(r.randint(0, 3)):
            k = ann_key(r, [kk for kk, _ in out] + ["figram", "requires", "conditions", "calls", "variables", "bot"],
                        plain)
            insert_at(r, out, (k, rand_value(r)))
    if plain:
        insert_at(r, out, (r.choice([k for k in UNKNOWN_KEYS if not out.has(k)]), rand_value(r)))
    if shuffle:
        out = shuffle_known(out, ["figram", "requires", "conditions", "calls", "variables", "bot"])
    return out


def variants(base_text, seed):
    r = random.Random(seed)
    j = load_j(base_text)
    ws_pool = ["", " ", "\t", "\r\n", "\n", "  "]
    out = [("V0", base_text)]
    out.append(("V1", write(j, ascii_=True)))
    out.append(("V2", write(transform(j, r, True, True, False, True))))
    out.append(("V3", write(transform(j, r, False, False, True, False), ascii_=r.random() < 0.5)))
    out.append(("V4", write(transform(j, r, True, True, True, True), ws=lambda: r.choice(ws_pool),
                            ascii_=r.random() < 0.5)))
    out.append(("V5", write(transform(j, r, False, False, True, False, plain=True))))
    return out


class Tmp:
    def __init__(self, d):
        self.d = d
        self.n = 0

    def file(self, text):
        self.n += 1
        p = os.path.join(self.d, f"doc{self.n % 64}.json")
        with open(p, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        return p


def check_doc(name, base_text, seed, tmp, brk, stats, fails):
    base_p = tmp.file(base_text)
    rc, base_stats, _ = run([FIGRAM, "stats", base_p])
    rc2, base_trace, _ = run([FIGRAM, "trace", base_p, "3", "8", str(seed)])
    if rc or rc2:
        fails.append((name, "base", "base document not usable", rc, rc2))
        return
    for vname, text in variants(base_text, seed):
        stats["variants"] += 1
        p = tmp.file(text)
        rc, out, err = run([FIGRAM, "fmt", p])
        exp = oracle_fmt(text, brk)
        # P1
        stats["P1"] += 1
        if exp is None or rc != 0 or out != exp:
            fails.append((name, vname, "P1 fmt != oracle", rc, err.decode()[:200],
                          (out[:300], (exp or b"<reject>")[:300])))
            continue
        # P2
        p2 = tmp.file(out.decode())
        rc, out2, err2 = run([FIGRAM, "fmt", p2])
        stats["P2"] += 1
        if rc != 0 or out2 != out:
            fails.append((name, vname, "P2 fmt not idempotent", rc, err2.decode()[:200]))
            continue
        # P3 structure via python json with object_pairs_hook
        stats["P3"] += 1
        model = Canon(brk).doc(load_j(text))
        if load_j(out.decode()) != model:
            fails.append((name, vname, "P3 python reload != oracle model"))
            continue
        # P4 behaviour unchanged; a key without x- is an error of the document
        rc, ck, _ = run([FIGRAM, "check", p])
        rc_s, st, _ = run([FIGRAM, "stats", p])
        if vname == "V5":
            stats["P4-reject"] += 1
            if rc == 0 or b"unknown key" not in ck or rc_s == 0:
                fails.append((name, vname, "P4 unknown key not rejected", rc, ck[:200], rc_s))
            continue
        rc_t, tr, _ = run([FIGRAM, "trace", p, "3", "8", str(seed)])
        stats["P4"] += 1
        if rc or ck != b"ok\n" or rc_s or st != base_stats or rc_t or tr != base_trace:
            fails.append((name, vname, "P4 behaviour changed", rc, ck[:200], st == base_stats, tr == base_trace))


# Rows that exercise finding FC-01 (printer writes an exponent that the
# parser rejects).  Expected to fail on the baseline.
EDGE = [
    ("FC-01 annotation 1.0e-1000",
     '{"figram":1,"x-ui":{"zoom":1.0e-1000},"bot":{"initial":"a","children":{"a":{}}}}'),
    ("FC-01 annotation -2.50e-999",
     '{"figram":1,"bot":{"initial":"a","children":{"a":{"x-ui":[-2.50e-999]}}}}'),
    ("FC-01 annotation 1001 fraction digits",
     '{"figram":1,"bot":{"initial":"a","children":{"a":{"on":{"btn:x":[{"to":"a","x-p":0.'
     + "0" * 1000 + '1}]}}}}}'),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=250)
    ap.add_argument("--break", dest="brk", default=None,
                    help="deliberate oracle bug: sort-unknown | keep-resume-false | keep-num-text | drop-unknown")
    ap.add_argument("--strict", action="store_true", help="count known-finding rows as failures")
    ap.add_argument("--tmp", default=os.environ.get("TMPDIR", "/tmp"))
    a = ap.parse_args()
    tmpdir = os.path.join(a.tmp, f"fc-a-{os.getpid()}")
    os.makedirs(tmpdir, exist_ok=True)
    tmp = Tmp(tmpdir)
    stats = {"docs": 0, "variants": 0, "P1": 0, "P2": 0, "P3": 0, "P4": 0, "P4-reject": 0}
    fails = []
    docs = []
    for seed in range(a.seeds):
        n = 1 + seed % 15
        rc, out, err = run([FIGRAM, "random", str(seed), str(n)])
        if rc != 0:
            fails.append((f"random {seed} {n}", "gen", rc, err.decode()[:200]))
            continue
        docs.append((f"random {seed} {n}", out.decode(), seed))
    for p in sorted(glob.glob(os.path.join(ROOT, "examples", "*.bot.json"))):
        with open(p, encoding="utf-8") as f:
            docs.append((os.path.relpath(p, ROOT), f.read(), 7))
    for name, text, seed in docs:
        stats["docs"] += 1
        check_doc(name, text, seed, tmp, a.brk, stats, fails)
    # known-finding rows
    edge_fail = 0
    for name, text in EDGE:
        p = tmp.file(text)
        rc, out, err = run([FIGRAM, "fmt", p])
        exp = oracle_fmt(text, a.brk)
        ok1 = rc == 0 and out == exp
        rc2, out2, err2 = run([FIGRAM, "fmt", tmp.file(out.decode())]) if rc == 0 else (None, b"", b"")
        ok2 = rc2 == 0 and out2 == out
        print(f"EDGE {name}: fmt rc={rc} oracle-match={ok1} refmt rc={rc2} idempotent={ok2}"
              + ("" if ok2 else f"  refmt stderr: {err2.decode().strip()[:120]}"))
        edge_fail += not (ok1 and ok2)
    for f in fails[:15]:
        print("FAIL", f)
    print(f"docs={stats['docs']} variants={stats['variants']} P1={stats['P1']} P2={stats['P2']} "
          f"P3={stats['P3']} P4={stats['P4']} P4-reject={stats['P4-reject']} failures={len(fails)} edge_fail={edge_fail}/{len(EDGE)}"
          f" break={a.brk}")
    if stats["P4"] == 0 or (stats["P4-reject"] == 0 and not fails):
        print("no comparisons made")
        sys.exit(2)
    bad = len(fails) + (edge_fail if a.strict else 0)
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()

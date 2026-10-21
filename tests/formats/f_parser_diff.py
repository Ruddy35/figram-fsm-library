#!/usr/bin/env python3
"""(f) Differential test of two builds of the JSON text layer: OLD vs NEW `figram json`.

Written for the rewrite of the parser (from `List Char` to byte positions, hash
set of keys, divide-and-conquer digit conversion and number printing): the
accepted language, the parsed value, the printed text AND the error text must be
byte-identical.  OLD is the build before a change, NEW the build after it.

Inputs (all valid UTF-8, one per line, no LF inside a line):
  * every text row of the RFC edge table (b_rfc_table.T);
  * well-formed random values (b_rfc_table.r_text);
  * random texts and their mutations (json_fuzz.rand_text / mutate) — mostly
    rejected, so the ERROR TEXT of each failure position is compared;
  * targeted fragments around escapes (`\\u` with < 4 characters left, multibyte
    characters after `\\u` / `\\`, surrogate pairs), numbers (leading zeros, empty
    fraction/exponent, exponent 1000/1001 with leading zeros), literals and
    separators, raw control characters and NUL, nesting 255…258;
  * larger values: objects with 8…3000 keys (with and without a duplicate at a
    random place — the switch from linear search to the key set is at 8 keys),
    numbers with 18…5000 digits, long strings with escapes.

    python3 f_parser_diff.py OLD_FIGRAM NEW_FIGRAM [--n 60000] [--seed 7]

Exit 0 iff every output line is identical, at least --n inputs were compared and
every error message of the parser occurred at least once (coverage of the
failure positions, MESSAGES below).
"""

import argparse
import json
import os
import random
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "engine"))
import b_rfc_table  # noqa: E402
import json_fuzz  # noqa: E402

FRAG = [
    "\\u", "\\u1", "\\u12", "\\u123", "\\u1234", "\\uD800", "\\uDBFF", "\\uDC00", "\\uDFFF", "\\uD83D\\uDE00",
    "\\uD800\\u", "\\uD800\\u12", "\\uD800\\uD800", "\\uD800x", "\\u00e9", "\\u0000", "\\uFFFF", "\\uFFFE", "\\uabcg",
    "\\", "\\\\", "\\\"", "\\/", "\\b", "\\f", "\\n", "\\r", "\\t", "\\x", "\\é", "\\😀", "\\u12é", "\\u1😀",
    '"', "é", "😀", "Ж", "\x00", "\x01", "\x1f", "\x7f", " ", "﻿", "￿", "\t", "\r", " ",
    "0", "-0", "-", "01", "-01", "00", "1.", ".5", "1.5", "1e", "1e+", "1e-", "1E5", "1e1000", "1e1001", "1e-1000",
    "1e-1001", "1e0001000", "1e00001001", "-0.0", "0e0", "123456789012345678901234567890", "1.0e-1000",
    "true", "tru", "false", "fals", "null", "nul", "truex", "nullx", "NaN", "Infinity",
    "{", "}", "[", "]", ":", ",", '{"a":', '{"a":1', '{"a":1,', '{"a":1,"a":2}', "[1,]", "{,}", '{"a"}', '{"a" 1}',
    "[[", "]]", "{}", "[]", '""', '"\\u"', '"x', "x",
]


# Every error text of the parser (FormatIO.lean); each must be produced by some input.
MESSAGES = [
    "invalid \\u escape", "truncated \\u escape", "unterminated string",
    "unpaired surrogate in \\u escape", "unknown escape sequence", "control character in string",
    "expected a digit", "missing digit after `.`", "missing digit in exponent",
    "exponent out of range: absolute value above 1000", "nesting deeper than 256", "leading zeros are not allowed",
    "unexpected character", "unexpected end of input", "trailing comma before `]`", "expected `,` or `]`",
    "trailing comma before `}`", "duplicate key", "expected `,` or `}`", "expected `:`",
    "expected a quoted key", "extra text after the JSON value",
]


def frag_text(r):
    t = "".join(r.choice(FRAG) for _ in range(r.randint(1, 6)))
    ctx = r.random()
    if ctx < 0.3:
        return '"' + t + '"'
    if ctx < 0.45:
        return "[" + t + "]"
    if ctx < 0.6:
        return '{"k":' + t + "}"
    if ctx < 0.7:
        return '{"' + t + '":1}'
    return t


def deep(r):
    d = r.choice([255, 256, 257, 258])
    o, c = r.choice([("[", "]"), ('{"k":', "}")])
    inner = r.choice(["1", "[]", "{}", '"x"', ""])
    return o * d + inner + c * r.choice([d, d - 1, d + 1])


def big_obj(r):
    n = r.choice([7, 8, 9, 15, 16, 17, 100, 1000, 3000])
    keys = [f"k{i}" for i in range(n)]
    if r.random() < 0.5:
        keys.insert(r.randrange(len(keys) + 1), keys[r.randrange(n)])   # duplicate somewhere
    return "{" + ",".join(json.dumps(k) + ":" + str(i) for i, k in enumerate(keys)) + "}"


def big_num(r):
    n = r.choice([17, 18, 19, 20, 37, 38, 39, 100, 1000, 5000])
    ds = str(r.randint(1, 9)) + "".join(r.choice("0123456789") for _ in range(n - 1))
    s = r.choice(["", "-"]) + ds
    if r.random() < 0.5:
        s += "." + "".join(r.choice("0123456789") for _ in range(r.choice([1, 18, 19, 500, 3000])))
    if r.random() < 0.5:
        s += r.choice("eE") + r.choice(["", "+", "-"]) + str(r.choice([0, 30, 31, 999, 1000, 1001]))
    return s


def big_str(r):
    parts = []
    for _ in range(r.randint(1, 200)):
        k = r.random()
        if k < 0.6:
            parts.append("".join(r.choice("abcйж😀 ") for _ in range(r.randint(1, 300))))
        else:
            parts.append(r.choice(["\\n", "\\u00e9", "\\uD83D\\uDE00", "\\\\", "\\\"", "\\t", "\\/"]))
    return '"' + "".join(parts) + '"'


def inputs(n, seed):
    r = random.Random(seed)
    out = [t for _, t, _, _ in b_rfc_table.T]
    quarter = n // 4
    out += [b_rfc_table.r_text(r) for _ in range(quarter)]
    for i in range(quarter):
        t = json_fuzz.rand_text(r)
        out.append(json_fuzz.mutate(r, t) if i % 2 else t)
    out += [frag_text(r) for _ in range(quarter)]
    rest = n - len(out)
    for i in range(max(rest, 1000)):
        out.append([deep, big_obj, big_num, big_str][i % 4](r))
    # one case per line: no LF inside; raw surrogates cannot be encoded in UTF-8
    return ["".join("?" if 0xD800 <= ord(c) <= 0xDFFF else c for c in t).replace("\n", " ") for t in out]


def run(figram, cases):
    data = ("\n".join(cases) + "\n").encode("utf-8")
    p = subprocess.run([figram, "json"], input=data, capture_output=True, timeout=1800)
    lines = p.stdout.split(b"\n")[:-1]
    if p.returncode != 0 or len(lines) != len(cases):
        raise SystemExit(f"{figram} json: rc={p.returncode}, {len(lines)} answers for {len(cases)} lines; "
                         f"{p.stderr[:200]!r}")
    return lines


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("old")
    ap.add_argument("new")
    ap.add_argument("--n", type=int, default=60000)
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()
    cases = inputs(a.n, a.seed)
    old, new = run(a.old, cases), run(a.new, cases)
    diff = ok = err = 0
    seen = set()
    for t, o, w in zip(cases, old, new):
        if o.startswith(b"ERR "):
            text = json.loads(o[4:].decode("utf-8"))
            seen.update(m for m in MESSAGES if text.startswith(m))
        if o != w:
            diff += 1
            if diff <= 10:
                print(f"DIFF {t[:120]!r}\n  old {o[:160]!r}\n  new {w[:160]!r}")
        elif o.startswith(b"OK "):
            ok += 1
        else:
            err += 1
    missing = [m for m in MESSAGES if m not in seen]
    print(f"inputs={len(cases)} identical={len(cases) - diff} (accepted {ok}, rejected {err}) differences={diff} "
          f"error-messages-covered={len(MESSAGES) - len(missing)}/{len(MESSAGES)} {missing}")
    sys.exit(0 if diff == 0 and len(cases) >= a.n and ok > 0 and err > 0 and not missing else 1)


if __name__ == "__main__":
    main()

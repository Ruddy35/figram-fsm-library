#!/usr/bin/env python3
"""(b) RFC 8259 edge-case table for the text layer (`figram json`).

Each row: (id, input bytes, expected `figram json` line, class).
  expected  "OK <compact>"  — exact compact print expected from Figram;
            "ERR"           — any rejection.
  class     rfc   — plain RFC 8259 behaviour (Python json agrees);
            dev   — documented Figram deviation (FormatIO.lean header,
                    FORMAT.md §3 «Числа», AUDIT.md §4): Python json differs;
            model — Figram's exact m·10^e model prints the value differently
                    from Python's float (value-equal);
            fc01 / fc02 / fc03 — rows of findings in REPORT.md.

Checks, for every row:
  T1  figram output == expected column (handwritten);
  T2  the independent oracle (Python json + documented deviations +
      re-implemented printer, common.py) produces the same line;
  T3  idempotence: every OK output fed back into `figram json` gives the same
      OK line (the printer only writes text the parser accepts).
Then R random well-formed values (nesting ≤ 5, short strings, small numbers
incl. exponents up to ±1000) are serialised with random spacing/escaping and
checked with T2+T3.

    python3 b_rfc_table.py [--random N] [--seed S] [--break dup-last-wins|no-surrogate|float]
                           [--strict]
"""

import argparse
import json
import os
import random
import sys
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import FIGRAM, Num, Obj, Reject, compact, load_j, run  # noqa: E402

D256A = "[" * 256 + "]" * 256
D257A = "[" * 257 + "]" * 257
D256O = '{"k":' * 255 + "{}" + "}" * 255
D257O = '{"k":' * 256 + "{}" + "}" * 256

T = [
    # ---- numbers ----------------------------------------------------------
    ("n-neg-zero", "-0", "OK 0", "rfc"),
    ("n-neg-zero-frac", "-0.0", "OK 0.0", "fc02"),        # Python: -0.0
    ("n-neg-zero-exp", "-0e0", "OK 0", "rfc"),
    ("n-neg-zero-arr", "[-0.00]", "OK [0.00]", "fc02"),
    ("n-1E+2", "1E+2", "OK 1e2", "model"),                # Python: 100.0
    ("n-1e-2", "1e-2", "OK 0.01", "model"),
    ("n-0.0e-0", "0.0e-0", "OK 0.0", "rfc"),
    ("n-1.5e3", "1.5e3", "OK 15e2", "model"),             # Python: 1500.0
    ("n-trailing-zeros", "1.50", "OK 1.50", "model"),     # Python: 1.5
    ("n-int", "100", "OK 100", "rfc"),
    ("n-bigint", "123456789012345678901234567890", "OK 123456789012345678901234567890", "rfc"),
    ("n-0e5", "0e5", "OK 0e5", "model"),
    ("n-1e-30", "1e-30", "OK 0.000000000000000000000000000001", "model"),
    ("n-1e-31", "1e-31", "OK 1e-31", "model"),
    ("n-neg-frac", "-1.5", "OK -1.5", "rfc"),
    ("n-e1000", "1e1000", "OK 1e1000", "dev"),            # Python: inf
    ("n-e-1000", "1E-1000", "OK 1e-1000", "dev"),         # Python: 0.0
    ("n-e1001", "1e1001", "ERR", "dev"),                  # |exp| > 1000
    ("n-e-1001", "1e-1001", "ERR", "dev"),
    ("n-e+01000", "1e+01000", "OK 1e1000", "dev"),        # leading zeros in exponent are RFC-legal
    ("n-fc01-a", "1.0e-1000", "OK 10e-1001", "fc01"),     # printed text is rejected by the parser
    ("n-fc01-b", "-2.50e-999", "OK -250e-1001", "fc01"),
    ("n-minus", "-", "ERR", "rfc"),
    ("n-plus", "+1", "ERR", "rfc"),
    ("n-dot-lead", ".5", "ERR", "rfc"),
    ("n-dot-trail", "5.", "ERR", "rfc"),
    ("n-lead-zero", "01", "ERR", "rfc"),
    ("n-neg-lead-zero", "-01", "ERR", "rfc"),
    ("n-00", "00", "ERR", "rfc"),
    ("n-0.0-lead", "00.5", "ERR", "rfc"),
    ("n-hex", "0x10", "ERR", "rfc"),
    ("n-e-empty", "1e", "ERR", "rfc"),
    ("n-e-plus-empty", "1e+", "ERR", "rfc"),
    ("n-dot-e", "1.e5", "ERR", "rfc"),
    ("n-e-signs", "1.5e+-3", "ERR", "rfc"),
    ("n-inf", "Infinity", "ERR", "rfc"),                  # Python accepts by default
    ("n-nan", "NaN", "ERR", "rfc"),
    ("n-neg-inf", "-Infinity", "ERR", "rfc"),
    ("n-two", "1 2", "ERR", "rfc"),
    ("n-in-arr", "[1,-0,2.5E-3]", "OK [1,0,0.0025]", "model"),
    # ---- strings ----------------------------------------------------------
    ("s-escapes", r'"\b\f\n\r\t\/\"\\"', r'OK "\u0008\u000c\n\r\t/\"\\"', "rfc"),
    ("s-e-acute-raw", '"é"', 'OK "é"', "rfc"),
    ("s-e-acute-esc", r'"é"', 'OK "é"', "rfc"),
    ("s-e-acute-esc-upper", r'"é"', 'OK "é"', "rfc"),
    ("s-pair", r'"😀"', 'OK "😀"', "rfc"),
    ("s-pair-upper", r'"😀"', 'OK "😀"', "rfc"),
    ("s-pair-raw", '"😀"', 'OK "😀"', "rfc"),
    ("s-lone-high", r'"\ud800"', "ERR", "dev"),            # Python accepts
    ("s-lone-low", r'"\udc00"', "ERR", "dev"),
    ("s-high-nonlow", r'"\ud800A"', "ERR", "dev"),
    ("s-reversed-pair", r'"\ude00\ud83d"', "ERR", "dev"),
    ("s-high-high", r'"\ud800\ud800"', "ERR", "dev"),
    ("s-nul-esc", r'"\u0000"', r'OK "\u0000"', "rfc"),
    ("s-us-esc", r'"\u001f"', r'OK "\u001f"', "rfc"),
    ("s-esc-esc", r'"\u001b[31m"', r'OK "\u001b[31m"', "rfc"),
    ("s-raw-tab", '"a\tb"', "ERR", "rfc"),
    ("s-raw-cr", '"a\rb"', "ERR", "rfc"),
    ("s-raw-us", '"a\x1fb"', "ERR", "rfc"),
    ("s-raw-nul", '"a\x00b"', "ERR", "rfc"),
    ("s-raw-del", '"a\x7fb"', 'OK "a\x7fb"', "rfc"),
    ("s-raw-u2028", '" "', 'OK " "', "rfc"),
    ("s-raw-bom-inside", '"﻿"', 'OK "﻿"', "rfc"),
    ("s-bad-x", r'"\x41"', "ERR", "rfc"),
    ("s-short-u", r'"\u12"', "ERR", "rfc"),
    ("s-bad-hex", r'"\u12G4"', "ERR", "rfc"),
    ("s-squote-esc", r'"\'"', "ERR", "rfc"),
    ("s-bell", r'"\a"', "ERR", "rfc"),
    ("s-unterminated", '"abc', "ERR", "rfc"),
    ("s-unterminated-esc", '"\\"', "ERR", "rfc"),
    ("s-single-quotes", "'a'", "ERR", "rfc"),
    # ---- literals ---------------------------------------------------------
    ("l-true", "true", "OK true", "rfc"),
    ("l-false", "false", "OK false", "rfc"),
    ("l-null", "null", "OK null", "rfc"),
    ("l-truex", "truex", "ERR", "rfc"),
    ("l-true1", "true1", "ERR", "rfc"),
    ("l-arr-falsey", "[true,falsey]", "ERR", "rfc"),
    ("l-obj-nullx", '{"a":nullx}', "ERR", "rfc"),
    ("l-nul", "nul", "ERR", "rfc"),
    ("l-tru", "tru", "ERR", "rfc"),
    ("l-True", "True", "ERR", "rfc"),
    ("l-NULL", "NULL", "ERR", "rfc"),
    # ---- whitespace -------------------------------------------------------
    ("w-ascii", " \t\r1 \t\r", "OK 1", "rfc"),
    ("w-in-obj", '{ \t"a" \r: \t[ 1 ,\t2 ] }', 'OK {"a":[1,2]}', "rfc"),
    ("w-nbsp-lead", " 1", "ERR", "rfc"),
    ("w-nbsp-trail", "1 ", "ERR", "rfc"),
    ("w-nbsp-inside", "[1, 2]", "ERR", "rfc"),
    ("w-vt", "\x0b1", "ERR", "rfc"),
    ("w-ff", "\x0c1", "ERR", "rfc"),
    ("w-u2028", " 1", "ERR", "rfc"),
    ("w-ideographic", "　1", "ERR", "rfc"),
    ("w-bom", "﻿1", "ERR", "rfc"),                    # RFC: MAY ignore; Python rejects
    ("w-bom-obj", '﻿{"figram":1}', "ERR", "rfc"),
    ("w-nul-after", '{"a":1}\x00', "ERR", "rfc"),          # no C-string truncation at NUL
    ("w-nul-before", '\x00{"a":1}', "ERR", "rfc"),
    # ---- structure --------------------------------------------------------
    ("x-arr-trailing", "[1,]", "ERR", "rfc"),
    ("x-arr-lead", "[,1]", "ERR", "rfc"),
    ("x-arr-double", "[1,,2]", "ERR", "rfc"),
    ("x-obj-trailing", '{"a":1,}', "ERR", "rfc"),
    ("x-obj-comma", "{,}", "ERR", "rfc"),
    ("x-obj-nocolon", '{"a" 1}', "ERR", "rfc"),
    ("x-obj-novalue", '{"a":}', "ERR", "rfc"),
    ("x-obj-barekey", "{a:1}", "ERR", "rfc"),
    ("x-obj-numkey", "{1:1}", "ERR", "rfc"),
    ("x-open-arr", "[", "ERR", "rfc"),
    ("x-close-arr", "]", "ERR", "rfc"),
    ("x-open-obj", '{"a":1', "ERR", "rfc"),
    ("x-empty-arr", "[]", "OK []", "rfc"),
    ("x-empty-obj", "{}", "OK {}", "rfc"),
    ("x-nested-empty", "[[],{}]", "OK [[],{}]", "rfc"),
    ("x-empty-key", '{"":1}', 'OK {"":1}', "rfc"),
    ("x-key-order", '{"b":1,"a":2}', 'OK {"b":1,"a":2}', "rfc"),
    ("x-dup", '{"a":1,"a":1}', "ERR", "dev"),              # Python: last wins
    ("x-dup-escaped", '{"a":1,"\\u0061":2}', "ERR", "dev"),
    ("x-dup-nested", '[{"a":{"b":1,"b":2}}]', "ERR", "dev"),
    ("x-case-keys", '{"a":1,"A":2}', 'OK {"a":1,"A":2}', "rfc"),
    ("x-nfc-nfd-keys", '{"é":1,"e\\u0301":2}', 'OK {"é":1,"é":2}', "rfc"),
    ("x-depth-256-arr", D256A, "OK " + D256A, "dev"),
    ("x-depth-257-arr", D257A, "ERR", "dev"),
    ("x-depth-256-obj", D256O, "OK " + D256O, "dev"),
    ("x-depth-257-obj", D257O, "ERR", "dev"),
    # ---- empty input / top-level scalars ---------------------------------
    ("t-empty", "", "ERR", "rfc"),
    ("t-space", " ", "ERR", "rfc"),
    ("t-scalar-num", "1", "OK 1", "rfc"),
    ("t-scalar-str", '"x"', 'OK "x"', "rfc"),
    ("t-scalar-null", "null", "OK null", "rfc"),
    ("t-two-values", "{}{}", "ERR", "rfc"),
]

# Byte-level rows: invalid UTF-8 on the stdin line.  RFC 8259 §8.1: text MUST be
# UTF-8; Python json.loads(bytes) raises.  Figram's stream path replaces with
# U+FFFD (AUDIT.md §4: "невалидный UTF-8 … рантайм Lean заменяет символом U+FFFD").
TB = [
    ("u-ff", b'"\xff"', 'OK "�"', "fc03"),
    ("u-cesu-surrogate", b'"\xed\xa0\x80"', 'OK "�"', "fc03"),
    ("u-overlong-slash", b'"\xc0\xaf"', None, "fc03"),
    ("u-above-10ffff", b'"\xf4\x90\x80\x80"', None, "fc03"),
    ("u-truncated", b'"\xe2\x82"', None, "fc03"),
    ("u-ff-in-key", b'{"\xff":1,"\xfe":2}', None, "fc03"),
]


def oracle_line(text: str, brk=None) -> str:
    try:
        if brk == "dup-last-wins":
            j = load_j(text, deviations=False)
        elif brk == "no-surrogate":
            j = json.loads(text, object_pairs_hook=lambda p: Obj(p),
                           parse_int=lambda t: load_j(t), parse_float=lambda t: load_j(t),
                           parse_constant=lambda c: (_ for _ in ()).throw(Reject(c)))
        elif brk == "float":
            j = json.loads(text, object_pairs_hook=lambda p: Obj(p))
            return "OK " + json.dumps(j, separators=(",", ":"), ensure_ascii=False)
        else:
            j = load_j(text)
        return "OK " + compact(j)
    except TypeError:
        return "OK <value the printer cannot represent>"
    except (Reject, ValueError, RecursionError):
        return "ERR"


def norm(line: str) -> str:
    return "ERR" if line.startswith("ERR ") else line


def run_json(lines_bytes):
    data = b"".join(b + b"\n" for b in lines_bytes)
    rc, out, err = run([FIGRAM, "json"], data, timeout=120)
    outs = out.decode("utf-8").split("\n")[:-1]
    if rc != 0 or len(outs) != len(lines_bytes):
        raise SystemExit(f"figram json: rc={rc}, {len(outs)} answers for {len(lines_bytes)} lines; {err[:200]!r}")
    return outs


# ---------------------------------------------------------------- random ---

def r_str(r):
    pool = ["a", "b", "Z", "0", " ", "é", "Ж", "😀", " ", "\x00", "\x01", "\x1f", "\x7f", "\n", "\t",
            '"', "\\", "/", "﻿", "￿"]
    return "".join(r.choice(pool) for _ in range(r.randint(0, 5)))


def r_num_text(r):
    s = r.choice(["", "-"]) + r.choice(["0", str(r.randint(1, 99999))])
    if r.random() < 0.5:
        s += "." + "".join(r.choice("0123456789") for _ in range(r.randint(1, 5)))
    if r.random() < 0.5:
        e = r.choice([r.randint(0, 40), r.randint(990, 1000), r.randint(0, 1000)])
        s += r.choice("eE") + r.choice(["", "+", "-"]) + str(e)
    return s


def r_text(r, depth=0):
    """Well-formed JSON text (by RFC and the documented restrictions)."""
    ws = lambda: r.choice(["", "", " ", "\t", "\r", "  \t"])  # no LF: one case per line
    k = r.random()
    if depth >= 5 or k < 0.35:
        c = r.random()
        if c < 0.4:
            return r_num_text(r)
        if c < 0.8:
            return json.dumps(r_str(r), ensure_ascii=r.random() < 0.5)
        return r.choice(["true", "false", "null"])
    if k < 0.65:
        return "[" + ws() + ("," + ws()).join(r_text(r, depth + 1) + ws() for _ in range(r.randint(0, 4))) + "]"
    keys = list(dict.fromkeys(r_str(r) for _ in range(r.randint(0, 4))))
    return "{" + ws() + ",".join(ws() + json.dumps(kk, ensure_ascii=r.random() < 0.5) + ws() + ":" + ws()
                                 + r_text(r, depth + 1) + ws() for kk in keys) + "}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--random", type=int, default=4000)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--break", dest="brk", default=None)
    ap.add_argument("--strict", action="store_true")
    a = ap.parse_args()

    rows = [(i, t.encode("utf-8"), exp, cls) for i, t, exp, cls in T] + list(TB)
    outs = run_json([b for _, b, _, _ in rows])
    t1 = t2 = 0
    fails, known = [], []
    reparse = []
    for (rid, b, exp, cls), got in zip(rows, outs):
        got_n = norm(got)
        try:
            text = b.decode("utf-8")
            ora = oracle_line(text, a.brk)
        except UnicodeDecodeError:
            ora = "ERR"   # RFC / Python: not UTF-8 -> reject
        ok1 = exp is None or got_n == exp
        ok2 = got_n == ora
        t1 += 1
        t2 += 1
        if got.startswith("OK "):
            reparse.append((rid, cls, got))
        row = f"{rid:22s} class={cls:5s} figram={got[:70]!r} expected={str(exp)[:50]!r} oracle={ora[:50]!r}"
        print(("    " if ok1 and ok2 else "DIFF ") + row)
        if not ok1:
            fails.append(("T1", rid))
        if not ok2:
            (known if cls.startswith("fc") else fails).append(("T2", rid))
    # T3 idempotence of OK outputs, by figram itself
    outs2 = run_json([g[3:].encode("utf-8") for _, _, g in reparse])
    t3 = 0
    for (rid, cls, g), g2 in zip(reparse, outs2):
        t3 += 1
        if g2 != g:
            print(f"DIFF T3 {rid}: {g[:60]!r} -> {g2[:80]!r}")
            (known if cls.startswith("fc") else fails).append(("T3", rid))

    # random well-formed values
    r = random.Random(a.seed)
    rnd = [r_text(r) for _ in range(a.random)]
    routs = run_json([t.encode("utf-8") for t in rnd])
    r_ok = r_fc01 = 0
    re_in = []
    for t, got in zip(rnd, routs):
        ora = oracle_line(t, a.brk)
        if norm(got) != ora:
            fails.append(("R", t[:80], got[:80], ora[:80]))
        else:
            r_ok += 1
        if got.startswith("OK "):
            re_in.append(got)
    re_out = run_json([g[3:].encode("utf-8") for g in re_in])
    r_idem = 0
    for g, g2 in zip(re_in, re_out):
        if g2 == g:
            r_idem += 1
        elif "показатель" in g2:
            r_fc01 += 1          # FC-01: printer wrote |exponent| > 1000
            known.append(("R-T3", g[:60]))
        else:
            fails.append(("R-T3", g[:80], g2[:80]))
    for f in fails[:12]:
        print("FAIL", f)
    print(f"table rows={len(rows)} T1={t1} T2={t2} T3={t3}; random={len(rnd)} oracle-equal={r_ok} "
          f"reparsed={len(re_in)} idempotent={r_idem} fc01-hits={r_fc01}; failures={len(fails)} "
          f"known-finding-diffs={len(known)} break={a.brk}")
    if not rows or not rnd:
        sys.exit(2)
    sys.exit(1 if fails or (a.strict and known) else 0)


if __name__ == "__main__":
    main()

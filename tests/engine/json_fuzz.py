"""Differential testing of the JSON text layer (FormatIO.lean).

The reference is the json module of the Python standard library plus the
deliberate restrictions of the format: a duplicate key, an unpaired surrogate in
\\u, nesting deeper than 256, an exponent above 1000 in absolute value and
NaN/Infinity are rejected. On every input it compares:

* whether the text is accepted by both or rejected by both;
* if accepted, the exact value (numbers as Decimal, key order counts);
* printing is idempotent: parsing our output gives the same value, and Figram
  itself reads its output back into the same output.

    python3 json_fuzz.py FIGRAM [cases] [seed]
"""

import json
import os
import random
import re
import sys
from decimal import Decimal, InvalidOperation

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "clients", "python"))
from protocol import default_timeout, run_child

MAX_DEPTH, MAX_EXP = 256, 1000


class Reject(Exception):
    pass


def pairs_hook(pairs):
    keys = [k for k, _ in pairs]
    if len(keys) != len(set(keys)):
        raise Reject("duplicate key")
    return ("obj", tuple(pairs))


def no_const(_):
    raise Reject("NaN/Infinity")


def load(text):
    return json.loads(text, parse_float=Decimal, parse_int=Decimal,
                      parse_constant=no_const, object_pairs_hook=pairs_hook)


def walk(v, depth=0):
    """Checks the restrictions of the format on a parsed value."""
    if isinstance(v, str):
        if any(0xD800 <= ord(c) <= 0xDFFF for c in v):
            raise Reject("unpaired surrogate")
    elif isinstance(v, list):
        if depth + 1 > MAX_DEPTH:
            raise Reject("depth")
        for x in v:
            walk(x, depth + 1)
    elif isinstance(v, tuple):
        if depth + 1 > MAX_DEPTH:
            raise Reject("depth")
        for k, x in v[1]:
            walk(k, depth + 1)
            walk(x, depth + 1)


NUM = re.compile(r"-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE]([+-]?\d+))?")


def exponents_ok(text):
    """Exponents of the numbers outside strings."""
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == '"':
            i += 1
            while text[i] != '"':
                i += 2 if text[i] == "\\" else 1
            i += 1
        elif c == "-" or c.isdigit():
            m = NUM.match(text, i)
            if m.group(1) is not None and abs(int(m.group(1))) > MAX_EXP:
                return False
            i = m.end()
        else:
            i += 1
    return True


def expected(text):
    try:
        v = load(text)
        walk(v)
        if not exponents_ok(text):
            raise Reject("exponent")
        return ("ok", v)
    except (ValueError, Reject, RecursionError, InvalidOperation):
        # InvalidOperation: an exponent that does not fit even into Decimal; the rules
        # of the format reject such a text.
        return ("err", None)


# --- generation ---------------------------------------------------------------

def rand_char(r):
    k = r.random()
    if k < 0.5:
        return chr(r.randint(0x20, 0x7E))
    if k < 0.6:
        return chr(r.randint(0, 0x1F))
    if k < 0.75:
        return chr(r.randint(0xA0, 0xFFFF))
    if k < 0.85:
        return chr(r.randint(0x10000, 0x10FFFF))
    if k < 0.9:
        return chr(r.randint(0xD800, 0xDFFF))   # unpaired surrogate
    return r.choice('"\\/\b\f\n\r\t')


def rand_str(r):
    return "".join(rand_char(r) for _ in range(r.randint(0, 8)))


def rand_num_text(r):
    s = r.choice(["", "-"]) + r.choice(["0", str(r.randint(1, 9)) + "".join(
        r.choice("0123456789") for _ in range(r.randint(0, 25)))])
    if r.random() < 0.4:
        s += "." + "".join(r.choice("0123456789") for _ in range(r.randint(1, 20)))
    if r.random() < 0.4:
        e = r.choice([r.randint(0, 30), r.randint(990, 1010), r.randint(0, 5000)])
        s += r.choice("eE") + r.choice(["", "+", "-"]) + str(e)
    return s


def rand_text(r, depth=0):
    """A JSON text (not always valid under the restrictions of the format)."""
    ws = lambda: r.choice(["", "", " ", "\t", "\r", "  "])
    k = r.random()
    if depth > 4 and k < 0.9 or k < 0.3:
        c = r.random()
        if c < 0.35:
            return rand_num_text(r)
        if c < 0.75:
            return json.dumps(rand_str(r), ensure_ascii=r.random() < 0.5)
        # NaN/Infinity are constants forbidden by the format: without them the restriction would go untested
        return r.choice(["true", "false", "null", "true", "false", "null", "NaN", "Infinity", "-Infinity"])
    if k < 0.65:
        items = [rand_text(r, depth + 1) for _ in range(r.randint(0, 4))]
        return "[" + ws() + ("," + ws()).join(items) + ws() + "]"
    keys = [rand_str(r) for _ in range(r.randint(0, 4))]
    if keys and r.random() < 0.1:
        keys.append(keys[0])                    # duplicate key
    items = [json.dumps(k_, ensure_ascii=r.random() < 0.5) + ws() + ":" + ws() + rand_text(r, depth + 1)
             for k_ in keys]
    return "{" + ws() + ("," + ws()).join(items) + ws() + "}"


def deep_text(r):
    d = r.choice([255, 256, 257, 300])
    o = r.choice(["[", '{"k":'])
    c = "]" if o == "[" else "}"
    return o * d + "1" + c * d if o == "[" else o * d + "1" + c * d


def mutate(r, t):
    t = list(t)
    for _ in range(r.randint(1, 3)):
        if not t:
            break
        i = r.randrange(len(t))
        op = r.random()
        ch = r.choice('{}[],:"\\0123456789eE.-+ tfnalsrue') if r.random() < 0.8 else rand_char(r)
        if op < 0.33:
            del t[i]
        elif op < 0.66:
            t.insert(i, ch)
        else:
            t[i] = ch
    return "".join(t)


def main():
    figram = sys.argv[1]
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 20000
    r = random.Random(int(sys.argv[3]) if len(sys.argv) > 3 else 1)
    cases = []
    for i in range(n):
        t = rand_text(r)
        if i % 50 == 0:
            t = deep_text(r)
        elif i % 2 == 1:
            t = mutate(r, t)
        # A raw unpaired surrogate cannot be encoded in UTF-8, so a real text has none;
        # escaped ones (\\udXXX) stay and are checked.
        t = "".join("?" if 0xD800 <= ord(c) <= 0xDFFF else c for c in t)
        cases.append(t.replace("\n", " "))      # one line per case
    res = run_child([figram, "json"], ("\n".join(cases) + "\n").encode(), default_timeout(), "json")
    if res.returncode != 0:
        raise SystemExit(f"the parser exited with code {res.returncode}")
    raw = res.stdout
    out = raw.decode().split("\n")[:-1]
    if n < 1 or len(out) != len(cases):
        raise SystemExit(f"replies {len(out)} of {len(cases)}")
    accepted = rejected = bad = 0
    for text, line in zip(cases, out):
        exp, val = expected(text)
        if line.startswith("OK "):
            got = "ok"
            try:
                ours = load(line[3:])
            except Exception as e:  # noqa: BLE001
                ours = ("unreadable", repr(e))
        else:
            got, ours = "err", None
        ok = got == exp and (exp == "err" or ours == val)
        if ok:
            accepted += exp == "ok"
            rejected += exp == "err"
        else:
            bad += 1
            if bad <= 5:
                print(f"  MISMATCH: {text[:150]!r}\n    reference {exp}, ours {line[:150]!r}")
    # Idempotence by Figram itself: Figram reads every "OK …" output back into the same
    # output. Python does not fit here: it accepts exponents above 1000.
    printed = [line[3:] for line in out if line.startswith("OK ")]
    res2 = run_child([figram, "json"], ("\n".join(printed) + "\n").encode(), default_timeout(), "json")
    again = res2.stdout.decode().split("\n")[:-1]
    if res2.returncode != 0 or len(again) != len(printed):
        raise SystemExit(f"second parse: exit code {res2.returncode}, replies {len(again)} of {len(printed)}")
    unread = 0
    for p_, a_ in zip(printed, again):
        if a_ != "OK " + p_:
            unread += 1
            if unread <= 5:
                print(f"  DOES NOT READ BACK: {p_[:150]!r}\n    {a_[:150]!r}")
    print(f"cases {n}: accepted by both {accepted}, rejected by both {rejected}, mismatches {bad}, "
          f"outputs not read back {unread} of {len(printed)}")
    sys.exit(1 if bad or unread else 0)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""R05 model parser for json_fuzz.py sensitivity analysis.
Usage (as json_fuzz.py "FIGRAM" argument): model_parser.py json
Env FIGRAM_MODEL_RELAX = comma list of constraints to DROP: dup,depth,exp,surrogate,const
Implements the json_fuzz.py oracle exactly (so with no relaxation it must give 0 mismatches)."""
import json, os, sys
from decimal import Decimal
sys.setrecursionlimit(100000)
RELAX = set(filter(None, os.environ.get("FIGRAM_MODEL_RELAX", "").split(",")))
MAX_DEPTH, MAX_EXP = 256, 1000
class Reject(Exception): pass
def hook(pairs):
    keys = [k for k, _ in pairs]
    if "dup" not in RELAX and len(keys) != len(set(keys)): raise Reject()
    return ("obj", tuple(pairs))
def const(c):
    if "const" not in RELAX: raise Reject()
    return Decimal(c.replace("Infinity", "Inf"))
def walk(v, d=0):
    if isinstance(v, str):
        if "surrogate" not in RELAX and any(0xD800 <= ord(c) <= 0xDFFF for c in v): raise Reject()
    elif isinstance(v, (list, tuple)):
        if "depth" not in RELAX and d + 1 > MAX_DEPTH: raise Reject()
        items = v if isinstance(v, list) else [x for kv in v[1] for x in kv]
        for x in items: walk(x, d + 1)
sys.path.insert(0, os.environ["JSON_FUZZ_DIR"])
from json_fuzz import exponents_ok  # same lexer as the oracle
def dump(v):
    if isinstance(v, tuple): return "{" + ",".join(json.dumps(k) + ":" + dump(x) for k, x in v[1]) + "}"
    if isinstance(v, list): return "[" + ",".join(dump(x) for x in v) + "]"
    if isinstance(v, Decimal): return str(v) if v.is_finite() else "1e99999"
    if v is True: return "true"
    if v is False: return "false"
    if v is None: return "null"
    return json.dumps(v)   # str; lone surrogates become \udXXX escapes
assert sys.argv[1:] == ["json"]
for line in sys.stdin:
    t = line.rstrip("\n")
    try:
        v = json.loads(t, parse_float=Decimal, parse_int=Decimal, parse_constant=const, object_pairs_hook=hook)
        walk(v)
        if "exp" not in RELAX and not exponents_ok(t): raise Reject()
        print("OK " + dump(v), flush=False)
    except Exception:
        print('ERR "x"', flush=False)
sys.stdout.flush()

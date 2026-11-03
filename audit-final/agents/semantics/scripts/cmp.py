#!/usr/bin/env python3
"""cmp.py ACTUAL EXPECTED: compare engine replies with prose-derived expectations, by id.
Objects are compared as JSON values (key order ignored), lists in order. Prints MISMATCH lines."""
import json, sys
act = [json.loads(l) for l in open(sys.argv[1]) if l.strip()]
exp = [json.loads(l) for l in open(sys.argv[2]) if l.strip() and not l.startswith('#')]
bad = 0
if len(act) != len(exp):
    print(f"COUNT actual={len(act)} expected={len(exp)}"); bad += 1
for a, e in zip(act, exp):
    if a.get('id') != e.get('id'):
        print(f"ID-ORDER actual={a.get('id')} expected={e.get('id')}"); bad += 1; continue
    ee = dict(e)
    # an expected error is matched by code only (texts are for humans)
    if 'code' in ee:
        ok = a.get('code') == ee['code'] and set(a) == {'id', 'error', 'code'}
    else:
        ok = a == ee
    if not ok:
        bad += 1
        print(f"MISMATCH id={e.get('id')}\n  expected: {json.dumps(e)}\n  actual:   {json.dumps(a)}")
    else:
        print(f"ok id={e.get('id')}")
print(f"TOTAL {len(exp)} mismatches {bad}")
sys.exit(1 if bad else 0)

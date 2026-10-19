#!/usr/bin/env python3
"""Regression test for the HOST.md reset / upgrade contract (engine-timers audit).

    python3 check_host_contract.py HOST.md FORMAT.md ENGINE EXAMPLES_DIR

Executes the procedure HOST.md §4 prescribes for a user whose snapshot is lost
against the real engine and requires that no idempotency key `user:seq:index`
(HOST.md §1.4) of the new life collides with a key of the old life. Also checks
that the docs state the two other hazards found by the audit (stale snapshot for
restart; timers across document versions). Exit 0 = all checks pass.
"""

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ptdriver import Engine  # noqa: E402

ORDER = [("btn:order", None), ("btn:cart", None), ("btn:checkout", {"cart_not_empty": True}),
         ("msg:text", {"in_delivery_zone": True}), ("payment:success", None)]


def section(text, num):
    m = re.search(rf"^## {num}\..*?(?=^## \d+\.|\Z)", text, re.S | re.M)
    return m.group(0) if m else ""


def call(e, req):
    raw, rid = e.call(req)
    r = json.loads(raw)
    assert r.get("id") == rid
    return r


def life(e, first):
    r = first
    out = [r]
    for ev, g in ORDER:
        req = {"op": "step", "snapshot": r["snapshot"], "event": ev}
        if g:
            req["guards"] = g
        r = call(e, req)
        out.append(r)
    return out


def keys(replies):
    k = {}
    for r in replies:
        for i, a in enumerate(r["actions"]):
            k[f"u:{r['snapshot'].get('seq', 0)}:{i}"] = a
    return k


def main(host_md, format_md, engine, examples):
    host = open(host_md, encoding="utf-8").read()
    fmt = open(format_md, encoding="utf-8").read()
    s4, s6 = section(host, 4), section(host, 6)
    fails = []

    # 1. the documented lost-snapshot procedure
    # the doc may write the high-water mark as a placeholder `N`; the test plugs in the real value
    reqs = [json.loads(re.sub(r'"seq":\s*N\b', '"seq": 0', m))
            for m in re.findall(r'^\{.*"op":\s*"restart".*\}$', s4, re.M)]
    if reqs:
        proc = "restart with placeholder snapshot " + json.dumps(reqs[0]["snapshot"], ensure_ascii=False)
    elif re.search(r"снимок утерян.*?`start`", s4, re.S):
        proc = "start"
    else:
        proc = None
        fails.append("§4: no procedure for a lost snapshot found")
    if proc:
        e = Engine(engine, os.path.join(examples, "pizza.bot.json"))
        l1 = life(e, call(e, {"op": "start"}))
        k1 = keys(l1)
        high_water = max(int(k.split(":")[1]) for k in k1)
        if proc == "start":
            first = call(e, {"op": "start"})
        else:
            snap = dict(reqs[0]["snapshot"])
            snap["seq"] = high_water          # N = largest seq ever issued to the user
            first = call(e, {"op": "restart", "snapshot": snap})
        l2 = life(e, first)
        k2 = keys(l2)
        coll = sorted(k for k in k2 if k in k1)
        lost = [k for k in coll if k2[k].get("call") in ("create_order", "save_address")]
        e.close()
        print(f"§4 lost-snapshot procedure: {proc}")
        print(f"   life1 keys {len(k1)}, life2 keys {len(k2)}, colliding {len(coll)}; "
              f"irreversible calls deduplicated away: {[(k, k2[k]) for k in lost]}")
        if coll:
            fails.append(f"§4 procedure '{proc}' reuses {len(coll)} idempotency keys (incl. {lost})")

    # 2. restart must use the LATEST committed snapshot
    if not (re.search(r"последн\w* зафиксированн", s4)):
        fails.append("§4 does not require the latest committed snapshot for restart (stale snapshot reuses seq)")
    # 3. FORMAT §7: start is unsafe also because of idempotency keys
    p = fmt[fmt.find("`restart` отменяет таймеры снимка"):][:900]
    if "ключ" not in p:
        fails.append("FORMAT.md §7: `start` caveat mentions timers only, not idempotency keys")
    # 4. HOST §6 says what happens to timers across document versions
    if "таймер" not in s6:
        fails.append("HOST.md §6 says nothing about timers of the old document version")

    for f in fails:
        print("FAIL:", f)
    print("OK" if not fails else f"{len(fails)} failure(s)")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:5]))

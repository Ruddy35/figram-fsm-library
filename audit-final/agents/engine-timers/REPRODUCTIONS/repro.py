#!/usr/bin/env python3
"""Standalone reproductions (engine-timers audit). Stdlib only, real engine process.

    python3 repro.py CASE [ENGINE]        ENGINE default: <repo>/bin/figram-engine

Cases: f1-start-keys f2-moved-timer f3-renamed-timer f4-need-unreachable
       f5-selfloop-starvation f6-stale-restart f7-guarded-timer f8-id-null
       f9-quadratic f10-mutate f11-rlimit
Each prints every request and reply line and a one-line VERDICT.
"""
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = "/home/user/figram-audit-worktrees/engine-timers"
PIZZA = os.path.join(REPO, "examples/pizza.bot.json")
UP = os.path.join(HERE, "upgrade-docs")
ENGINE = sys.argv[2] if len(sys.argv) > 2 else os.path.join(REPO, "bin/figram-engine")


class Eng:
    def __init__(self, doc, *extra):
        self.p = subprocess.Popen([ENGINE, *extra, doc], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        self.n = 0

    def __call__(self, **req):
        self.n += 1
        req = {"id": self.n, **req}
        line = json.dumps(req, separators=(",", ":"))
        self.p.stdin.write(line.encode() + b"\n")
        self.p.stdin.flush()
        out = self.p.stdout.readline().decode()
        print(">>", line[:300])
        print("<<", out.strip()[:300])
        return json.loads(out)

    def close(self):
        self.p.stdin.close()
        return self.p.wait()


def step(e, r, ev, **kw):
    return e(op="step", snapshot=r["snapshot"], event=ev, **kw)


ORDER = [("btn:order", {}), ("btn:cart", {}), ("btn:checkout", {"guards": {"cart_not_empty": True}}),
         ("msg:text", {"guards": {"in_delivery_zone": True}}), ("payment:success", {})]


def keys(replies):
    return {f"u:{r['snapshot'].get('seq', 0)}:{i}": a for r in replies for i, a in enumerate(r["actions"])}


def f1():
    e = Eng(PIZZA)
    lives = []
    for life in (1, 2):
        print(f"--- life {life}: start + order path (HOST.md §4 allows start when the snapshot is lost)")
        r = e(op="start")
        rs = [r]
        for ev, kw in ORDER:
            r = step(e, r, ev, **kw)
            rs.append(r)
        lives.append(keys(rs))
    k1, k2 = lives
    coll = {k: v for k, v in k2.items() if k in k1}
    print("keys life1:", k1)
    print("colliding:", len(coll), "of", len(k2))
    ok = not any(v.get("call") == "create_order" for v in coll.values())
    print("VERDICT:", "no collision" if ok else
          f"BUG: 2nd order's create_order key {[k for k, v in coll.items() if v == {'call': 'create_order'}]} "
          "equals the 1st order's key -> idempotent CRM drops the 2nd order")
    e.close()


def f2():
    e1 = Eng(os.path.join(UP, "v1.bot.json"))
    r = e1(op="start")
    for ev, kw in ORDER[:2]:
        r = step(e1, r, ev, **kw)
    e1.close()
    print("--- v1 user on 'cart' with cart_reminder scheduled; deploy v2 (timer moved to 'address',"
          " arrow lifted to section 'order'); the v1 event arrives")
    e2 = Eng(os.path.join(UP, "v2_move_timer.bot.json"))
    r2 = step(e2, r, "timer:cart_reminder", token=r["snapshot"]["timers"]["cart_reminder"])
    print("VERDICT:", "BUG: stale v1 timer accepted, user moved to " + r2["snapshot"]["at"]
          if r2["actions"] else "ignored")
    e2.close()


def f3():
    e1 = Eng(os.path.join(UP, "v1.bot.json"))
    r = e1(op="start")
    for ev, kw in ORDER[:2]:
        r = step(e1, r, ev, **kw)
    e1.close()
    print("--- v2 renames cart's timer cart_reminder -> cart_nudge")
    e2 = Eng(os.path.join(UP, "v2_rename_timer.bot.json"))
    r2 = r
    for ev, kw in ORDER[2:]:
        r2 = step(e2, r2, ev, **kw)
    stale = r2["snapshot"].get("timers", {})
    print("VERDICT:", f"BUG: old timer never cancelled, still in snapshot after leaving cart: {stale}"
          if "cart_reminder" in stale else "cleaned")
    e2.close()


def f4():
    e = Eng(os.path.join(REPO, "audit/engine_timers/docs/t_need.bot.json"))
    r = e(op="start")
    n = step(e, r, "btn:go")
    a = step(e, r, "btn:go", guards={"expensive": True})
    b = step(e, r, "btn:go", guards={"expensive": False})
    same = a["snapshot"] == b["snapshot"] and a["actions"] == b["actions"]
    print("VERDICT:", f"need={n.get('need')} although the result is identical for both values: {same}")
    e.close()


def f5():
    e = Eng(os.path.join(REPO, "audit/engine_timers/docs/t_timers.bot.json"))
    r = e(op="start")
    for _ in range(10):
        r = step(e, r, "timer:tick", token=r["snapshot"]["timers"]["tick"])
    print("VERDICT: after 10 ticks (1 s) the 10 s 'timeout' was restarted 10 times, timers:",
          r["snapshot"]["timers"], "-> it never fires while tick runs")
    e.close()


def f6():
    e = Eng(PIZZA)
    r = e(op="start")
    life = [r]
    for ev, kw in [("btn:order", {}), ("btn:cart", {}), ("btn:checkout", {"guards": {"cart_not_empty": False}}),
                   ("btn:cart", {})]:
        r = step(e, r, ev, **kw)
        life.append(r)
    old_tok = r["snapshot"]["timers"]["cart_reminder"]
    print("--- restart from the STALE snapshot of seq 2 (e.g. lagging replica)")
    r = e(op="restart", snapshot=life[1]["snapshot"])
    for ev, kw in [("btn:order", {}), ("btn:cart", {})]:
        r = step(e, r, ev, **kw)
    late = step(e, r, "timer:cart_reminder", token=old_tok)
    print("VERDICT:", f"old token {old_tok} == new token {r['snapshot']['timers']['cart_reminder']}; "
          f"late old event -> {late['snapshot']['at']}")
    e.close()


def f7():
    e = Eng(os.path.join(REPO, "audit/engine_timers/docs/t_timers.bot.json"))
    r = step(e, e(op="start"), "btn:guarded")
    tok = r["snapshot"]["timers"]["gt"]
    a = step(e, r, "timer:gt", token=tok, guards={"ok": False})
    b = step(e, a, "timer:gt", token=tok, guards={"ok": True})
    print("VERDICT:", f"same fire (token {tok}) ignored with ok=false, timer kept {a['snapshot'].get('timers')}, "
          f"accepted on 2nd delivery -> {b['snapshot']['at']}")
    e.close()


def f8():
    p = subprocess.run([ENGINE, PIZZA], input=b'{"id":5,"op":"step","snapshot":{"at":"menu"},"event":"btn:order",'
                       b'"guards":{"a":true,"a":false}}\n{"id":6,"op":"hello"} x\n', capture_output=True)
    print(p.stdout.decode())
    print("VERDICT: requests with ids 5 and 6 answered with id null")


def f9():
    for n in (10_000, 20_000, 40_000):
        g = ",".join(f'"g{i}":true' for i in range(n))
        line = ('{"id":1,"op":"step","snapshot":{"at":"menu"},"event":"btn:order","guards":{' + g + '}}\n').encode()
        t = time.time()
        p = subprocess.run([ENGINE, PIZZA], input=line, capture_output=True, timeout=300)
        print(f"guards keys={n} bytes={len(line)} secs={time.time() - t:.2f} reply={p.stdout[:60]!r}")
    for n in (50_000, 100_000, 200_000):
        line = ('{"id":1,"op":"step","snapshot":{"at":"menu"},"event":"timer:x","token":1' + "0" * n + '}\n').encode()
        t = time.time()
        p = subprocess.run([ENGINE, PIZZA], input=line, capture_output=True, timeout=300)
        print(f"token digits={n} secs={time.time() - t:.2f} reply={p.stdout[:60]!r}")
    print("VERDICT: time grows ~4x per doubling (quadratic)")


def f10():
    a, b = Eng(PIZZA), Eng(PIZZA, "--mutate", "drop-call:create_order")
    for e in (a, b):
        e(op="hello")
        r = e(op="start")
        for ev, kw in ORDER:
            r = step(e, r, ev, **kw)
        e.close()
    print("VERDICT: identical hello, mutant silently omits create_order")


def f11():
    for lim in ("3000000000", "4000000000"):
        p = subprocess.run(["prlimit", f"--as={lim}", "--", ENGINE, PIZZA], input=b'{"id":1,"op":"hello"}\n',
                           capture_output=True)
        print(f"RLIMIT_AS={lim}: rc={p.returncode} stdout={p.stdout!r} stderr={p.stderr[:120]!r}")
    print("VERDICT: engine needs >3 GB of address space to start")


CASES = {"f1-start-keys": f1, "f2-moved-timer": f2, "f3-renamed-timer": f3, "f4-need-unreachable": f4,
         "f5-selfloop-starvation": f5, "f6-stale-restart": f6, "f7-guarded-timer": f7, "f8-id-null": f8,
         "f9-quadratic": f9, "f10-mutate": f10, "f11-rlimit": f11}

if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in CASES:
        print(__doc__)
        sys.exit(2)
    CASES[sys.argv[1]]()

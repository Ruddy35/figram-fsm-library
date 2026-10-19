#!/usr/bin/env python3
"""Corrupting proxy around the real engine, for negative checks of the driver's
model-free invariants. EVIL_ENGINE=<real engine>, EVIL=<mode>; argv as figram-engine."""
import json, os, random, subprocess, sys
real = os.environ["EVIL_ENGINE"]
mode = os.environ.get("EVIL", "none")
p = subprocess.Popen([real] + sys.argv[1:], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
rnd = random.Random(5)
n = 0
for line in sys.stdin.buffer:
    p.stdin.write(line); p.stdin.flush()
    if not line.strip():
        continue
    out = p.stdout.readline()
    r = json.loads(out)
    n += 1
    acts = r.get("actions") or []
    if mode == "id" and n == 40: r["id"] = 10 ** 9
    if mode == "kind" and "need" in r: r["error"] = "x"
    if mode == "schema" and "snapshot" in r and n > 30: r["snapshot"]["seq"] = -1
    if mode == "order" and len(acts) >= 2: r["actions"] = acts[::-1]
    if mode == "timers" and "snapshot" in r and "timers" in r["snapshot"] and n > 30: del r["snapshot"]["timers"]
    if mode == "need-dup" and "need" in r: r["need"] = r["need"] + r["need"][:1]
    if mode == "nondet" and "snapshot" in r and rnd.random() < 0.3: r["actions"] = acts + [{"send": r["snapshot"]["at"]}]
    if mode == "guards" and "snapshot" in r and line.count(b'"undeclared_guard"'): r["actions"] = []
    if mode == "err-snap" and "error" in r: r["snapshot"] = {"at": "menu"}
    if mode == "seq" and "snapshot" in r and acts and n > 30: r["snapshot"]["seq"] = r["snapshot"].get("seq", 0) + 2
    sys.stdout.write(json.dumps(r, separators=(",", ":"), ensure_ascii=False) + "\n"); sys.stdout.flush()
p.stdin.close(); sys.exit(p.wait())

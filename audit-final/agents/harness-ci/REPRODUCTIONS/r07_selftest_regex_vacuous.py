#!/usr/bin/env python3
"""R07: harness_selftest.caught() regex matches the SUCCESS output of every checked tool,
so "распознана" degenerates to "no Traceback": any non-zero exit (for any reason) counts as
a recognised corruption.   Usage: python3 -I r07_selftest_regex_vacuous.py <repo-root>"""
import os, re, subprocess, sys
R = sys.argv[1]; P = os.path.join(R, "ffi/python-example"); E = os.path.join(R, "bin/figram-engine")
src = open(os.path.join(P, "harness_selftest.py"), encoding="utf-8").read()
import ast
lines = src.splitlines(); i = next(k for k, l in enumerate(lines) if l.startswith("DETECTED = "))
for j in range(i + 1, len(lines) + 1):
    try: tree = ast.parse("\n".join(lines[i:j])); break
    except SyntaxError: continue
ns = {"re": re}; exec(compile(tree, "DETECTED", "exec"), ns); DETECTED = ns["DETECTED"]
tr = subprocess.run([os.path.join(R, "bin/figram"), "trace", os.path.join(R, "examples/pizza.bot.json"), "20", "30", "7"],
                    capture_output=True, check=True).stdout
open("/dev/shm/r07.jsonl", "wb").write(tr)
doc = os.path.join(R, "examples/pizza.bot.json")
runs = {
  "conformance": [E, f"{doc}=/dev/shm/r07.jsonl"],
  "loadtest": [E, doc, "/dev/shm/r07.jsonl", "8", "1"],
  "snapshot_fixtures": [E, os.path.join(R, "examples/features.bot.json"), os.path.join(R, "snapshot.schema.json")],
}
bad = 0
for tool, args in runs.items():
    r = subprocess.run([sys.executable, os.path.join(P, tool + ".py")] + args, capture_output=True, text=True, cwd=P)
    out = r.stdout + r.stderr
    m = DETECTED.search(out)
    print(f"{tool}: rc={r.returncode} (real engine, success) ; DETECTED matches={bool(m)} on {(m.group(0) if m else None)!r} in {out.strip().splitlines()[0][:90]!r}")
    bad += bool(m) and r.returncode == 0
print(f"tools whose clean success output already satisfies caught(): {bad} of {len(runs)}")
sys.exit(0 if bad == 0 else 1)

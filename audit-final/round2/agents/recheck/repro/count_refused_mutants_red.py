#!/usr/bin/env python3
"""How many of the mutants that tests/compiler/run_all.sh's `mutants` step runs (mutants_red.py
--docs 8 --steps 100 --seed 7, at most 100 per kind) the engine refuses (--mutate exits 2 before
any reply), so that mutants_red counts them as killed. Run from tests/compiler.
  python3 count_refused_mutants_red.py ENGINE"""
import os, random, subprocess, sys, tempfile
sys.path.insert(0, os.getcwd())
import diff_random
from gen import dumps, to_json
eng = sys.argv[1]
tot = ref = 0; kinds = {}
for k in range(8):
    seed = 7 * 100003 + k
    _, model = diff_random.make_model(seed)
    text = dumps(to_json(model))
    with tempfile.NamedTemporaryFile("w", suffix=".bot.json", delete=False) as f:
        f.write(text)
    env = dict(os.environ, FIGRAM_ALLOW_MUTANTS="1")
    muts = subprocess.run([eng, "--mutants", f.name], capture_output=True, text=True, env=env).stdout.split()
    by = {}
    for m in muts: by.setdefault(m.split(":")[0], []).append(m)
    keep = set()
    for kind, ms in by.items():
        keep |= set(ms if len(ms) <= 100 else random.Random(seed).sample(ms, 100))
    for m in [m for m in muts if m in keep]:
        tot += 1
        r = subprocess.run([eng, "--mutate", m, f.name], input='{"op":"hello","id":1}\n', capture_output=True, text=True, env=env)
        if r.returncode != 0:
            ref += 1; kinds[m.split(":")[0]] = kinds.get(m.split(":")[0], 0) + 1
    os.unlink(f.name)
print(f"mutants run {tot}, refused by --mutate {ref}: {kinds}")

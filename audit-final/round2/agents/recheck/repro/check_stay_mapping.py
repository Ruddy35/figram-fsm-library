#!/usr/bin/env python3
"""mutants_red.classify maps `stay-reenter:k` to flat[k] of its own reconstruction of the engine's
row order. Check on every offered stay-reenter mutant of the 8 documents of the `mutants` step that
flat[k] is a variant without a target (to is None), i.e. the reconstruction picks the variant the
engine mutates. Run from tests/compiler.  python3 check_stay_mapping.py ENGINE"""
import os, subprocess, sys, tempfile
sys.path.insert(0, os.getcwd())
import diff_random, mutants_red
from gen import dumps, to_json
from oracle import Bot
eng = sys.argv[1]; n = bad = 0
for k in range(8):
    seed = 7 * 100003 + k
    _, model = diff_random.make_model(seed)
    text = dumps(to_json(model))
    with tempfile.NamedTemporaryFile("w", suffix=".bot.json", delete=False) as f:
        f.write(text)
    muts = subprocess.run([eng, "--mutants", f.name], capture_output=True, text=True,
                          env=dict(os.environ, FIGRAM_ALLOW_MUTANTS="1")).stdout.split()
    bot = Bot(text)
    rows = mutants_red.compiled_rows(bot)
    flat = [(r, i) for r in rows for i in range(len(r[2]))]
    for m in muts:
        if m.startswith("stay-reenter:"):
            n += 1
            (L, e, c), i = flat[int(m.split(":")[1])]
            if c[i].to is not None:
                bad += 1; print("MISMAPPED", seed, m, L, e, i, c[i])
    os.unlink(f.name)
print(f"stay-reenter mutants {n}, mapped to a variant with a target {bad}")
sys.exit(1 if bad or n == 0 else 0)

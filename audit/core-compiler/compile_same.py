#!/usr/bin/env python3
"""The compiled automaton of two builds has the same shape (P-06/P-07 regression).

  python3 compile_same.py --old-figram F --old-engine E --new-figram F --new-engine E [--docs N]

For the example documents and `figram random SEED N` documents: `figram stats`
(states, rows, branches, resumes, actions) and the full mutant list of
`figram-engine --mutants` (it enumerates rows and variants in automaton order:
`redirect:k`, `swap-branches:k`, …) must be identical."""

import argparse
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
ENV = dict(os.environ, FIGRAM_ALLOW_MUTANTS="1")


def run(args):
    p = subprocess.run(args, capture_output=True, text=True, timeout=300, env=ENV)
    return p.returncode, p.stdout


def main():
    ap = argparse.ArgumentParser()
    for k in ("old-figram", "old-engine", "new-figram", "new-engine"):
        ap.add_argument("--" + k, required=True)
    ap.add_argument("--docs", type=int, default=60)
    a = ap.parse_args()
    docs = [os.path.join(REPO, "examples", f) for f in ("pizza.bot.json", "features.bot.json")]
    tmp = tempfile.mkdtemp()
    for i in range(a.docs):
        rc, out = run([a.old_figram, "random", str(i + 1), str(5 + (i * 7) % 60)])
        p = os.path.join(tmp, f"r{i}.bot.json")
        with open(p, "w") as f:
            f.write(out)
        docs.append(p)
    bad = 0
    muts = 0
    for d in docs:
        for what, o, n in (("stats", [a.old_figram, "stats", d], [a.new_figram, "stats", d]),
                           ("mutants", [a.old_engine, "--mutants", d], [a.new_engine, "--mutants", d])):
            ro, rn = run(o), run(n)
            if what == "mutants":
                muts += len(ro[1].split())
            if ro != rn:
                bad += 1
                print("DIFF", what, d, ro[0], rn[0], ro[1][:200], "|", rn[1][:200])
    print(f"documents {len(docs)}, mutants compared {muts}, differences {bad}")
    sys.exit(0 if bad == 0 and muts > 0 else 1)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Integration test commit: the core-compiler suite expects the CC-01-fixed engine semantics by default
(CC01=fixed), the original baseline reading stays available as CC01=baseline.
    python3 apply_cc_suite_fixed_expectations.py WORKTREE
"""
import sys, os

W = sys.argv[1]
d = os.path.join(W, "audit/core-compiler")


def edit(path, pairs):
    p = os.path.join(d, path)
    s = open(p).read()
    for old, new in pairs:
        assert s.count(old) == 1, (path, old)
        s = s.replace(old, new)
    open(p, "w").write(s)


edit("run_all.sh", [
    ("""# "findings" steps are regression tests of the reported findings and are
# expected to exit 1 on the baseline.
set -u""",
     """# "findings" steps are regression tests of the reported findings and are
# expected to exit 1 on the baseline.
#
# CC01=fixed (default, integration): the oracle uses the CC-01 fix semantics
# (a timer fires only on the message that owns it; leaving a message cancels
# every snapshot timer), so diff-random / mutants / lint-gaps / designer-upgrades
# are RED on the baseline engine and green on the fixed one.
# CC01=baseline: the original expectations of the core-compiler agent (Lean
# reading of decision 7), green on the baseline engine.
set -u
CC01=${CC01:-fixed}; export CC01
D=""; UPW=1
if [ "$CC01" = fixed ]; then D="--oracle-opt timer_accept=owned --oracle-opt exit_cancels=snapshot"; UPW=0; fi"""),
    ("""step 0 diff-random timeout 840 python3 diff_random.py --engine "$ENGINE" --docs 600 --steps 120 --seed 1
""",
     """step 0 diff-random timeout 840 python3 diff_random.py --engine "$ENGINE" --docs 600 --steps 120 --seed 1 $D
"""),
    ("""       --oracle-bug $b --expect-red; done""",
     """       --oracle-bug $b --expect-red $D; done"""),
    ("""# ... and differs from it after document upgrades (CC-01 found by random search; expected exit 1)
step 1 designer-upgrades""",
     """# ... and differs from it after document upgrades (CC-01 found by random search; exit 1 on the
# baseline engine, 0 once CC-01 is fixed)
step $UPW designer-upgrades"""),
    ("""step 0 mutants timeout 840 python3 mutants_red.py --engine "$ENGINE" --docs 8 --steps 100 --out "$LOG/mutant_survivors.json\"""",
     """step 0 mutants timeout 840 python3 mutants_red.py --engine "$ENGINE" --docs 8 --steps 100 --out "$LOG/mutant_survivors.json" $D"""),
])

edit("mutants_red.py", [
    ("""    ap.add_argument("--out")
    args = ap.parse_args()""",
     """    ap.add_argument("--out")
    ap.add_argument("--oracle-opt", action="append", default=[],
                    help="k=v ambiguity switch of oracle.Options, passed to diff_random")
    args = ap.parse_args()"""),
    ("""        base = argparse.Namespace(engine=args.engine, steps=args.steps, mutate=None,
                                  oracle_bug=None, no_upgrade=True)""",
     """        base = argparse.Namespace(engine=args.engine, steps=args.steps, mutate=None,
                                  oracle_bug=None, no_upgrade=True, oracle_opt=args.oracle_opt)"""),
    ("""            a = argparse.Namespace(engine=args.engine, steps=args.steps, mutate=m,
                                   oracle_bug=None, no_upgrade=True)""",
     """            a = argparse.Namespace(engine=args.engine, steps=args.steps, mutate=m,
                                   oracle_bug=None, no_upgrade=True, oracle_opt=args.oracle_opt)"""),
])

edit("lint_gaps.py", [
    ("""import argparse
import json
""", """import argparse
import json
import os
"""),
    ("""    "timer_arrow_on_non_owner": lambda ev: ev["fire_on_a"] == [] and ev["fire_with_foreign_entry"] == "z",""",
     """    # CC-01 fixed: a foreign snapshot entry no longer fires the arrow on a non-owner (stays on a);
    # CC01=baseline: the original observation (the arrow fires, user moves to z).
    "timer_arrow_on_non_owner": lambda ev: ev["fire_on_a"] == [] and ev["fire_with_foreign_entry"] == (
        "z" if os.environ.get("CC01") == "baseline" else "a"),"""),
])
print("ok")

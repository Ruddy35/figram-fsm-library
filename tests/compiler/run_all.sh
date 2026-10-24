#!/bin/sh
# Compiler oracle/differential suite.
#   tests/compiler/run_all.sh [ENGINE] [FIGRAM] [LOGDIR]
# Defaults: .lake/build/bin/figram-engine, .lake/build/bin/figram, tests/compiler/logs (git-ignored).
# Every step is recorded with tests/lib/run_logged.sh (command, exit code, stdout/stderr).
# Green steps must exit 0; "red" steps are red-proofs (a broken oracle or a
# mutated engine) and must ALSO exit 0 (they run with --expect-red);
# "findings" steps are regression tests of the defects this suite found during the audit
# (H2, H3 in directed.py); they exit 1 on the baseline (commit d69bbaf, the code before the
# audit fixes; audit archive: commit 16c885a).
#
# CC01, CC04 and CC05 select the reading of three behaviours changed by those fixes:
#   CC01  timers: a timer fires only on the message that owns it, and leaving a message cancels
#         every snapshot timer (see decision 7, "Timer tokens", in docs/format.md);
#   CC04  `need` asks only for the guards that can still decide the step (lint_gaps.py);
#   CC05  `figram check` warns about designer mistakes (lint_gaps.py).
# fixed (the default) is the current engine: diff-random / mutants / lint-gaps / designer-upgrades
# are red on the baseline engine and green on the current one. CC01=baseline CC04=baseline
# CC05=baseline restores the original expectations, green only on the baseline engine.
set -u
CC01=${CC01:-fixed}; export CC01
D=""; UPW=1
if [ "$CC01" = fixed ]; then D="--oracle-opt timer_accept=owned --oracle-opt exit_cancels=snapshot"; UPW=0; fi
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../.." && pwd)
abs() { case $1 in /*) echo "$1";; *) echo "$PWD/$1";; esac; }
ENGINE=$(abs "${1:-$ROOT/.lake/build/bin/figram-engine}")
FIGRAM=$(abs "${2:-$ROOT/.lake/build/bin/figram}")
LOG=$(abs "${3:-$HERE/logs}")
RUN="$ROOT/tests/lib/run_logged.sh"
for b in "$ENGINE" "$FIGRAM"; do
  [ -x "$b" ] || { echo "not an executable: $b (run lake build or pass ENGINE FIGRAM)"; exit 2; }
done
cd "$HERE"
fail=0
step() { want=$1; name=$2; shift 2
  nice -n 10 "$RUN" "$LOG/$name" "$@" >/dev/null; rc=$?
  if [ "$rc" = "$want" ]; then echo "ok    $name (exit $rc)"; else echo "WRONG $name (exit $rc, want $want)"; fail=1; fi; }

step 0 directed-conformance python3 directed.py --engine "$ENGINE" --group conformance
if [ "$CC01" = fixed ]; then
  # the findings H2 (timers, CC01) and H3 (`need`, CC04) are fixed: the engine must pass them
  # against the designer reading of the oracle (exit 0); an engine with only the timer fix exits 1
  step 0 directed-findings  python3 directed.py --engine "$ENGINE" --group findings \
       --oracle-opt timer_accept=owned --oracle-opt exit_cancels=snapshot --oracle-opt need=min
else
  step 1 directed-findings  python3 directed.py --engine "$ENGINE" --group findings
fi
step 0 directed-designer-oracle python3 directed.py --oracle-only --group all \
     --oracle-opt timer_accept=owned --oracle-opt exit_cancels=snapshot --oracle-opt need=min
for m in drop-exit drop-remember resume-default drop-timer-start; do
  step 0 red-directed-$m python3 directed.py --engine "$ENGINE" --mutate $m --expect-red; done
step 0 diff-random timeout 840 python3 diff_random.py --engine "$ENGINE" --docs 600 --steps 120 --seed 1 $D
for b in no-exit resume-initial accept-any-token need-own-only first-variant-only calls-after-entry no-restart-self \
    start-enters-initial; do
  step 0 red-oracle-$b timeout 300 python3 diff_random.py --engine "$ENGINE" --docs 40 --steps 60 --seed 5 \
       --oracle-bug $b --expect-red $D; done
# the CC01 timer semantics (designer reading of the oracle) == the baseline within ONE document version ...
step 0 designer-same-version timeout 600 python3 diff_random.py --engine "$ENGINE" --docs 300 --steps 120 --seed 9 \
     --no-upgrade --no-perturb --no-exhaustive --oracle-opt timer_accept=owned --oracle-opt exit_cancels=snapshot
# ... and differ from it after document upgrades (how random search found H2; exit 1 on the
# baseline engine, 0 on the current one)
step $UPW designer-upgrades timeout 600 python3 diff_random.py --engine "$ENGINE" --docs 400 --steps 80 --seed 21 \
     --no-exhaustive --no-perturb --oracle-opt timer_accept=owned --oracle-opt exit_cancels=snapshot --out "$LOG/designer-upgrades"
step 0 mutants timeout 840 python3 mutants_red.py --engine "$ENGINE" --docs 8 --steps 100 --out "$LOG/mutant_survivors.json" $D
step 0 metamorphic timeout 840 python3 metamorphic.py --engine "$ENGINE" --docs 40 --steps 60
for t in rename_nodes annotations unreachable_resume shuffle_children; do
  step 0 red-metamorphic-$t timeout 300 python3 metamorphic.py --engine "$ENGINE" --docs 3 --steps 20 \
       --only $t --break-transform $t --expect-red; done
step 0 check-diff timeout 840 python3 check_diff.py --engine "$ENGINE" --docs 20
step 0 red-check-diff timeout 300 python3 check_diff.py --engine "$ENGINE" --docs 1 --oracle-accept-all --expect-red
step 0 lint-gaps python3 lint_gaps.py --figram "$FIGRAM" --engine "$ENGINE"
# the warnings of `figram check` (CC05=fixed): expected fragments, and none on clean documents
if [ "${CC05:-fixed}" = fixed ]; then step 0 lint-warnings python3 lint_warnings.py --figram "$FIGRAM"; fi
exit $fail

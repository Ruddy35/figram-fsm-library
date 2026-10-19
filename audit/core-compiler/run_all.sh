#!/bin/sh
# Audit agent core-compiler: full oracle/differential suite.
#   ./run_all.sh [ENGINE] [FIGRAM] [LOGDIR]
# Every step is recorded with run.sh (command, exit code, stdout/stderr).
# Green steps must exit 0; "red" steps are red-proofs (a broken oracle or a
# mutated engine) and must ALSO exit 0 (they run with --expect-red);
# "findings" steps are regression tests of the reported findings and are
# expected to exit 1 on the baseline.
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
ENGINE=${1:-$HERE/../../bin/figram-engine}
FIGRAM=${2:-$HERE/../../bin/figram}
LOG=${3:-$HERE/logs}
RUN=/home/user/figram-audit/run.sh
[ -x "$RUN" ] || RUN="$HERE/run_log.sh"
cd "$HERE"
fail=0
step() { want=$1; name=$2; shift 2
  nice -n 10 "$RUN" "$LOG/$name" "$@" >/dev/null; rc=$?
  if [ "$rc" = "$want" ]; then echo "ok    $name (exit $rc)"; else echo "WRONG $name (exit $rc, want $want)"; fail=1; fi; }

step 0 directed-conformance python3 directed.py --engine "$ENGINE" --group conformance
step 1 directed-findings    python3 directed.py --engine "$ENGINE" --group findings
step 0 directed-designer-oracle python3 directed.py --oracle-only --group all \
     --oracle-opt timer_accept=owned --oracle-opt exit_cancels=snapshot --oracle-opt need=min
for m in drop-exit drop-remember resume-default drop-timer-start; do
  step 0 red-directed-$m python3 directed.py --engine "$ENGINE" --mutate $m --expect-red; done
step 0 diff-random timeout 840 python3 diff_random.py --engine "$ENGINE" --docs 600 --steps 120 --seed 1
for b in no-exit resume-initial accept-any-token need-own-only first-variant-only calls-after-entry no-restart-self; do
  step 0 red-oracle-$b timeout 300 python3 diff_random.py --engine "$ENGINE" --docs 40 --steps 60 --seed 5 \
       --oracle-bug $b --expect-red; done
# proposed CC-01 semantics (oracle designer reading) == baseline within ONE document version ...
step 0 designer-same-version timeout 600 python3 diff_random.py --engine "$ENGINE" --docs 300 --steps 120 --seed 9 \
     --no-upgrade --no-perturb --no-exhaustive --oracle-opt timer_accept=owned --oracle-opt exit_cancels=snapshot
# ... and differs from it after document upgrades (CC-01 found by random search; expected exit 1)
step 1 designer-upgrades timeout 600 python3 diff_random.py --engine "$ENGINE" --docs 400 --steps 80 --seed 21 \
     --no-exhaustive --no-perturb --oracle-opt timer_accept=owned --oracle-opt exit_cancels=snapshot --out "$LOG/designer-upgrades"
step 0 mutants timeout 840 python3 mutants_red.py --engine "$ENGINE" --docs 8 --steps 100 --out "$LOG/mutant_survivors.json"
step 0 metamorphic timeout 840 python3 metamorphic.py --engine "$ENGINE" --docs 40 --steps 60
for t in rename_nodes annotations unreachable_resume shuffle_children; do
  step 0 red-metamorphic-$t timeout 300 python3 metamorphic.py --engine "$ENGINE" --docs 3 --steps 20 \
       --only $t --break-transform $t --expect-red; done
step 0 check-diff timeout 840 python3 check_diff.py --engine "$ENGINE" --docs 20
step 0 red-check-diff timeout 300 python3 check_diff.py --engine "$ENGINE" --docs 1 --oracle-accept-all --expect-red
step 0 lint-gaps python3 lint_gaps.py --figram "$FIGRAM" --engine "$ENGINE"
exit $fail

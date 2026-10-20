#!/bin/sh
# core-compiler suite (from the integration-cc scratch branch) on OLD (fresh baseline) and NEW (CC-01 fix)
# engines, plus the fixed-semantics ("designer") differential runs WITH upgrades and perturbations.
#   cc_suite.sh SUITE_DIR NEW_ENGINE NEW_FIGRAM TAG
set -u
S=$1; NEW=$2; NEWF=$3; TAG=$4
OLD=/home/user/figram-audit/fresh/figram-engine
OLDF=/home/user/figram-audit/fresh/figram
L=/home/user/figram-audit/agents/integration/LOGS
R=/home/user/figram-audit/run.sh
cd "$S" || exit 2
echo "== run_all.sh NEW ($TAG)"; nice -n 10 sh ./run_all.sh "$NEW" "$NEWF" "$L/cc-suite-NEW-$TAG"; echo "run_all NEW exit=$?"
echo "== run_all.sh OLD"; nice -n 10 sh ./run_all.sh "$OLD" "$OLDF" "$L/cc-suite-OLD-$TAG"; echo "run_all OLD exit=$?"
D="--oracle-opt timer_accept=owned --oracle-opt exit_cancels=snapshot"
for e in NEW OLD; do
  eval eng=\$$e
  $R "$L/cc-designer-full-$e-$TAG" nice -n 10 timeout 840 python3 diff_random.py --engine "$eng" --docs 600 --steps 120 --seed 1 $D >/dev/null
  echo "designer-full (upgrades+perturb+exhaustive, seed 1) $e exit=$? :: $(tail -1 $L/cc-designer-full-$e-$TAG.stdout)"
  $R "$L/cc-designer-upgrades-$e-$TAG" nice -n 10 timeout 600 python3 diff_random.py --engine "$eng" --docs 400 --steps 80 --seed 21 --no-exhaustive --no-perturb $D >/dev/null
  echo "designer-upgrades (seed 21) $e exit=$? :: $(tail -1 $L/cc-designer-upgrades-$e-$TAG.stdout)"
done

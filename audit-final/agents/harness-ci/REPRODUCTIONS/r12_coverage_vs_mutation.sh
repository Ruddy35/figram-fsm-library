#!/usr/bin/env bash
# R12: does a coverage drop in the trace generator go unnoticed? Coverage is printed but not
# asserted by baseline verify.sh; the mutation step catches it indirectly (uncovered branches ->
# surviving redirect mutants). Usage: r12_coverage_vs_mutation.sh <scratch-dir> <threshold 23..99>
# (baseline threshold is 23). Needs lean/lake (source lean-overlay env). Engine: bin (CI-verified).
set -euo pipefail
D=$1; X=$2; rm -rf "$D"; mkdir -p "$D"
git -C /home/user/figram-fsm-library archive audit/baseline | tar -x -C "$D"
cp -a /home/user/figram-audit/fresh/lake-cache "$D/.lake"
cd "$D"
sed -i "s/  if roll < 23 then return (.named \"unknown:event\", g)/  if roll < $X then return (.named \"unknown:event\", g)/" Figram/Trace.lean
lake build figram 2>&1 | tail -1
for d in pizza features; do .lake/build/bin/figram trace examples/$d.bot.json 200 40 7 > "$D/$d.jsonl"; done
set +e
python3 ffi/python-example/mutation.py bin/figram-engine --golden examples/golden.jsonl \
  examples/pizza.bot.json="$D/pizza.jsonl" examples/features.bot.json="$D/features.jsonl" | grep -E "обнаружено|выжила" | head -4
echo "mutation.py exit: ${PIPESTATUS[0]}"

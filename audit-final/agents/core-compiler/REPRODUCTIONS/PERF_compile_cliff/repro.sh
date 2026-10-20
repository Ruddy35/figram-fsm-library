#!/bin/sh
# Compile/check cost grows ~ messages x events x depth x nodes: load time of figram-engine and
# figram check for a few shapes (limit 300 s each). Usage: repro.sh [ENGINE] [FIGRAM]
ENGINE=${1:-/home/user/figram-audit-worktrees/core-compiler/bin/figram-engine}
FIGRAM=${2:-/home/user/figram-audit-worktrees/core-compiler/bin/figram}
cd "$(dirname "$0")"
for spec in "flat 1000 1" "flat 2000 1" "flat 2000 50" "hist 50 20" "hist 100 20"; do
  set -- $spec; f=/tmp/figram_perf_$1_$2_$3.json; python3 gen.py $1 $2 $3 > $f
  s=$(date +%s.%N); echo '{"id":1,"op":"hello"}' | timeout 300 "$ENGINE" $f >/dev/null; rc=$?; e=$(date +%s.%N)
  s2=$(date +%s.%N); timeout 300 "$FIGRAM" check $f >/dev/null; rc2=$?; e2=$(date +%s.%N)
  echo "$spec bytes=$(wc -c <$f) engine_load_s=$(echo "$e - $s" | bc) rc=$rc  check_s=$(echo "$e2 - $s2" | bc) rc=$rc2"
  rm -f $f
done

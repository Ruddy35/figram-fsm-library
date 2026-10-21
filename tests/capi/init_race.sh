#!/usr/bin/env bash
# Concurrent initialization in N fresh processes.
#   tests/capi/init_race.sh BIN N [SUBTEST]   — exit code 0 only if all N processes succeed.
#   SUBTEST: init_race (default; two threads, explicit figram_init) or
#            lazy_race (8 threads, no figram_init: lazy initialization in load/request).
cd "$(dirname "$0")/../.."
BIN=${1:-tests/capi/out/contract}; N=${2:-200}; SUB=${3:-init_race}
declare -A codes; fail=0
for i in $(seq 1 "$N"); do
  timeout 20 "$BIN" "$SUB" examples/pizza.bot.json > /dev/null 2>&1; rc=$?
  codes[$rc]=$(( ${codes[$rc]:-0} + 1 ))
  [ $rc -ne 0 ] && fail=$((fail+1))
done
printf '%s: %d runs, failed %d; exit codes:' "$SUB" "$N" "$fail"
for k in "${!codes[@]}"; do printf ' %s×%s' "$k" "${codes[$k]}"; done; echo
[ "$fail" -eq 0 ]

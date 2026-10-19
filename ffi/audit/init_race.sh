#!/usr/bin/env bash
# Регрессионный тест: одновременный figram_init из двух потоков, N свежих процессов.
#   ffi/audit/init_race.sh BIN N   — код 0, только если все N процессов успешны.
cd "$(dirname "$0")/../.."
BIN=${1:-ffi/audit/out/contract}; N=${2:-200}
declare -A codes; fail=0
for i in $(seq 1 "$N"); do
  timeout 20 "$BIN" init_race examples/pizza.bot.json > /dev/null 2>&1; rc=$?
  codes[$rc]=$(( ${codes[$rc]:-0} + 1 ))
  [ $rc -ne 0 ] && fail=$((fail+1))
done
printf 'init_race: %d runs, failed %d; exit codes:' "$N" "$fail"
for k in "${!codes[@]}"; do printf ' %s×%s' "$k" "${codes[$k]}"; done; echo
[ "$fail" -eq 0 ]

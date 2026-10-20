#!/usr/bin/env bash
# Регрессионный тест: одновременный figram_init из двух потоков, N свежих процессов.
#   tests/capi/init_race.sh BIN N [ПОДТЕСТ]   — код 0, только если все N процессов успешны.
#   ПОДТЕСТ: init_race (по умолчанию; два потока, явный figram_init) или
#            lazy_race (8 потоков, без figram_init: ленивая инициализация в load/request).
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

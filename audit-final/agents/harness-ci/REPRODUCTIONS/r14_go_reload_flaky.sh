#!/usr/bin/env bash
# R14: Go reload memory check (main.go: withClose >= 2048 KB fails) under CPU load.
# Usage: r14_go_reload_flaky.sh <repo-root-with-built-gofig> <traces.jsonl> [runs]
R=${1:?}; TR=${2:?}; N=${3:-10}; fails=0
for i in $(seq "$N"); do
  out=$(cd "$R" && timeout 300 ffi/go-example/gofig -reload 5000 examples/pizza.bot.json "$TR" 2>&1); rc=$?
  echo "run $i rc=$rc :: $(echo "$out" | grep -m1 'загрузок')"; [ $rc -eq 0 ] || fails=$((fails+1))
done
echo "go reload: $fails failures of $N"; [ $fails -eq 0 ]

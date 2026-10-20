#!/usr/bin/env bash
# R02: client_contract.py state() catches FileNotFoundError only; reading /proc/PID/stat of a
# process reaped between open() and read() raises ProcessLookupError -> Traceback -> step red.
# Usage: r02_client_contract_flaky.sh <repo-root> [runs]
R=${1:?repo root}; N=${2:-20}; fails=0
for i in $(seq "$N"); do
  if ! out=$(timeout 120 python3 "$R/ffi/python-example/client_contract.py" "$R/bin/figram-engine" "$R/examples/features.bot.json" 2>&1); then
    fails=$((fails+1)); echo "run $i: FAIL: $(echo "$out" | grep -E "^[A-Za-z]+Error|^НЕТ" | tail -1)"
  fi
done
echo "client_contract.py: $fails failures of $N runs"
[ "$fails" -eq 0 ]

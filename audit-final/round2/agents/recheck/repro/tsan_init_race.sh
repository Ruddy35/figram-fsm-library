#!/usr/bin/env bash
# init_race under TSan N times (598fec6 barrier): count runs with a ThreadSanitizer report.
B=$1; D=$2; N=$3; bad=0
for i in $(seq 1 $N); do
  out=$(TSAN_OPTIONS="halt_on_error=1 suppressions=$PWD/tests/capi/tsan.supp" timeout 120 "$B" init_race "$D" 2>&1); rc=$?
  if [ $rc -ne 0 ] || printf '%s' "$out" | grep -q "WARNING: ThreadSanitizer"; then bad=$((bad+1)); fi
done
echo "tsan init_race runs $N, failed $bad"
[ $bad = 0 ]

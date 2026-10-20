#!/usr/bin/env bash
# figram_init под RLIMIT_AS = занято + N КиБ, N = 0…1024 шагом 16: исход каждого запуска (код, первая строка итога).
C=$1
for k in $(seq 0 16 1024); do
  out=$(FIGRAM_TEST_AS_HEADROOM=$((k * 1024)) "$C" rlimit_init 2>&1); rc=$?
  echo "$k KiB rc=$rc $(printf '%s' "$out" | grep -m1 -oE 'figram_init=[0-9]+ last_error=[^:]*|INTERNAL PANIC.*|terminate.*|what\(\).*')"
done

#!/usr/bin/env bash
# ASan/UBSan and TSan variants of the regression subtests (run tests/capi/build.sh first).
# Limitation: the Lean heap is mimalloc, which ASan/valgrind do not see; under TSan concurrent
# loads produce hundreds of false "races" because mimalloc reuses pages (the allocator is not
# instrumented), so lazy_race and the stress test with loader threads do not run under TSan.
# The real oracle is the functional stress test that checks every reply (stress.c).
# The lazy initialization of closed terms (lean_obj_once) is published by the uninstrumented
# runtime; those false reports (and only those) are suppressed in tests/capi/tsan.supp.
cd "$(dirname "$0")/../.."
B=tests/capi/out; D=examples/pizza.bot.json; D2=examples/features.bot.json; fail=0
run() { local name=$1; shift; out=$(ASAN_OPTIONS=detect_leaks=0 UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1 TSAN_OPTIONS="halt_on_error=1 suppressions=$PWD/tests/capi/tsan.supp" timeout 300 "$@" 2>&1); rc=$?
  if [ $rc -eq 0 ] && ! printf '%s' "$out" | grep -qE "ERROR: AddressSanitizer|runtime error:|WARNING: ThreadSanitizer"; then echo "PASS $name"; else echo "FAIL $name rc=$rc"; printf '%s\n' "$out" | grep -E "^FAIL|Sanitizer|runtime error" | head -8; fail=1; fi; }
for v in asan tsan; do
  run "$v lazy" $B/contract_$v lazy $D
  [ $v = asan ] && run "$v lazy_race" $B/contract_$v lazy_race $D
  run "$v null_safe" $B/contract_$v null_safe $D
  run "$v lifetime" $B/contract_$v lifetime $D $D2
  run "$v lengths" $B/contract_$v lengths $D
  run "$v utf8_strict" $B/contract_$v utf8_strict $D
  run "$v limit" $B/contract_$v limit $D
  run "$v signals custom" $B/contract_$v signals custom
  run "$v init_race" $B/contract_$v init_race $D
done
run "asan stress 16x5000+4" $B/stress_asan $D 16 5000 4
run "tsan stress 16x2000 (no loaders)" $B/stress_tsan $D 16 2000 0
run "asan harness" $B/harness_asan $D
exit $fail

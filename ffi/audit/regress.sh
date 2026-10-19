#!/usr/bin/env bash
# Регрессионные тесты находок ffi-go (C-уровень). Код 0 — все исправлены.
#   ffi/audit/regress.sh [BIN_DIR]   (сначала ffi/audit/build_audit.sh)
set -u
cd "$(dirname "$0")/../.."
B=${1:-ffi/audit/out}; D=examples/pizza.bot.json; fail=0
check() {  # имя ожидаемый_код команда…
  local name=$1 want=$2; shift 2
  out=$(timeout 60 "$@" 2>&1); rc=$?
  if [ "$rc" -eq "$want" ]; then echo "PASS $name (rc=$rc)"; else echo "FAIL $name: rc=$rc, want $want"; printf '%s\n' "$out" | tail -3 | sed 's/^/    /'; fail=1; fi
}
# FFI-01: одновременный figram_init из двух потоков (300 свежих процессов).
check "FFI-01 concurrent figram_init" 0 ffi/audit/init_race.sh "$B/contract" 300
# FFI-05: отказ malloc в dup() — NULL вместо SIGSEGV.
check "FFI-05 malloc failure in dup" 0 "$B/oom"
# FFI-06: figram_unload(NULL) — как free(NULL); в т.ч. после неудачной загрузки.
check "FFI-06 figram_unload(NULL)" 0 "$B/contract" null unload $D
check "FFI-06 unload(failed load)" 0 "$B/contract" null unload_failed_load $D
# Не регрессировало: основная обвязка и нагрузка с выгрузкой других автоматов.
check "harness 16x20000 + reload" 0 "$B/harness_plain" $D memory
check "stress 16x20000 + 4 loaders" 0 "$B/stress" $D 16 20000 4
exit $fail

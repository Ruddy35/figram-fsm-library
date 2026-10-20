#!/usr/bin/env bash
# Регрессионные тесты C-ABI (находки ffi-go FFI-01…FFI-10 и исправления fix-ffi). Код 0 — все исправлены.
#   ffi/audit/regress.sh [BIN_DIR]   (сначала ffi/audit/build_audit.sh)
set -u
cd "$(dirname "$0")/../.."
B=${1:-ffi/audit/out}; D=examples/pizza.bot.json; D2=examples/features.bot.json; fail=0
check() {  # имя ожидаемый_код команда…
  local name=$1 want=$2; shift 2
  out=$(timeout 120 "$@" 2>&1); rc=$?
  if [ "$rc" -eq "$want" ]; then echo "PASS $name (rc=$rc)"
  else
    echo "FAIL $name: rc=$rc, want $want"
    printf '%s\n' "$out" | grep -E "^FAIL|terminate|what\(\)" | head -12 | sed 's/^/    /'
    printf '%s\n' "$out" | tail -2 | sed 's/^/    /'; fail=1
  fi
}
# check_out имя регвыр команда… — код 0 И в выводе есть строка по регулярному выражению.
check_out() {
  local name=$1 rx=$2; shift 2
  out=$(timeout 120 "$@" 2>&1); rc=$?
  if [ "$rc" -eq 0 ] && printf '%s\n' "$out" | grep -qE "$rx"; then echo "PASS $name (rc=0, «$rx»)"
  else
    echo "FAIL $name: rc=$rc, want 0 and «$rx»"
    printf '%s\n' "$out" | grep -E "^FAIL|terminate|what\(\)|figram_init=" | head -12 | sed 's/^/    /'; fail=1
  fi
}
# FFI-01: одновременный figram_init из двух потоков (300 свежих процессов).
check "FFI-01 concurrent figram_init" 0 ffi/audit/init_race.sh "$B/contract" 300
# FFI-05: отказ malloc в dup() — NULL вместо SIGSEGV.
check "FFI-05 malloc failure in dup" 0 "$B/oom"
# FFI-06: figram_unload(NULL) — как free(NULL); в т.ч. после неудачной загрузки.
check "FFI-06 figram_unload(NULL)" 0 "$B/contract" null unload $D
check "FFI-06 unload(failed load)" 0 "$B/contract" null unload_failed_load $D
# P-09 (FFI-10/NP-01): figram_init под RLIMIT_AS — код ошибки с причиной, а не SIGABRT хоста.
check_out "P-09 init under RLIMIT_AS=1.5GB → rc 2 + reason" '^figram_init=2 last_error=.*thread' \
  prlimit --as=1500000000 -- "$B/contract" rlimit_init
check_out "P-09 init under RLIMIT_AS=3GB (C host: fits)" '^figram_init=0 ' \
  prlimit --as=3000000000 -- "$B/contract" rlimit_init
# P-21 (FFI-03, I-17): вызовы до figram_init — ленивая потокобезопасная инициализация.
check "P-21 lazy init (load/request before figram_init)" 0 "$B/contract" lazy $D
check "P-21 lazy init race, 8 threads x 300 processes" 0 ffi/audit/init_race.sh "$B/contract" 300 lazy_race
# P-21 (FFI-04, FFI-07): NULL, чужой указатель, use-after-unload, двойная выгрузка.
check "P-21 NULL arguments and foreign handle" 0 "$B/contract" null_safe $D
for w in check fmt load line handle; do check "P-21 null $w: no SIGSEGV" 0 "$B/contract" null $w $D; done
check "P-21 use-after-unload / double unload detected" 0 "$B/contract" lifetime $D $D2
# P-21 (FFI-08, NP-06): функции с длиной, load_doc_ex с причиной, NUL в тексте ошибки.
check "P-21 length variants, load_doc_ex, NUL in output" 0 "$B/contract" lengths $D
# P-18 (FC-03, NP-03): невалидный UTF-8 — parse_error / отказ документа.
check "P-18 strict UTF-8 on the FFI path" 0 "$B/contract" utf8_strict $D
# PROTOCOL_CHANGES 5, 8: предел размера запроса (too_large), пустой запрос (parse_error).
check "P-18 request size limit and empty request" 0 "$B/contract" limit $D
# P-22 (FFI-09, NP-02): SIGPIPE хоста и sigaltstack вызывающего потока сохраняются.
check "P-22 signals preserved (defaults)" 0 "$B/contract" signals default
check "P-22 signals preserved (custom handler + altstack)" 0 "$B/contract" signals custom
# Не регрессировало: основная обвязка и нагрузка с выгрузкой других автоматов.
check "harness 16x20000 + reload" 0 "$B/harness_plain" $D memory
check "stress 16x20000 + 4 loaders" 0 "$B/stress" $D 16 20000 4
exit $fail

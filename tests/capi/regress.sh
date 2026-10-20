#!/usr/bin/env bash
# Регрессионные тесты C-ABI (находки ffi-go FFI-01…FFI-10 и исправления fix-ffi). Код 0 — все исправлены.
#   tests/capi/regress.sh [BIN_DIR]   (сначала tests/capi/build.sh)
set -u
cd "$(dirname "$0")/../.."
B=${1:-tests/capi/out}; D=examples/pizza.bot.json; D2=examples/features.bot.json; fail=0; n=0; nfail=0
check() {  # имя ожидаемый_код команда…
  local name=$1 want=$2; shift 2
  out=$(timeout 120 "$@" 2>&1); rc=$?; n=$((n+1))
  if [ "$rc" -eq "$want" ]; then echo "PASS $name (rc=$rc)"
  else
    nfail=$((nfail+1))
    echo "FAIL $name: rc=$rc, want $want"
    printf '%s\n' "$out" | grep -E "^FAIL|terminate|what\(\)" | head -12 | sed 's/^/    /'
    printf '%s\n' "$out" | tail -2 | sed 's/^/    /'; fail=1
  fi
}
# check_out имя регвыр команда… — код 0 И в выводе есть строка по регулярному выражению.
check_out() {
  local name=$1 rx=$2; shift 2
  out=$(timeout 120 "$@" 2>&1); rc=$?; n=$((n+1))
  if [ "$rc" -eq 0 ] && printf '%s\n' "$out" | grep -qE "$rx"; then echo "PASS $name (rc=0, «$rx»)"
  else
    nfail=$((nfail+1))
    echo "FAIL $name: rc=$rc, want 0 and «$rx»"
    printf '%s\n' "$out" | grep -E "^FAIL|terminate|what\(\)|figram_init=" | head -12 | sed 's/^/    /'; fail=1
  fi
}
# check_rc_out имя код регвыр команда… — заданный код выхода И строка по регулярному выражению.
check_rc_out() {
  local name=$1 want=$2 rx=$3; shift 3
  out=$(timeout 120 "$@" 2>&1); rc=$?; n=$((n+1))
  if [ "$rc" -eq "$want" ] && printf '%s\n' "$out" | grep -qE "$rx"; then echo "PASS $name (rc=$rc, «$rx»)"
  else
    nfail=$((nfail+1))
    echo "FAIL $name: rc=$rc, want $want and «$rx»"
    printf '%s\n' "$out" | tail -3 | sed 's/^/    /'; fail=1
  fi
}
# FFI-01: одновременный figram_init из двух потоков (300 свежих процессов).
check "FFI-01 concurrent figram_init" 0 tests/capi/init_race.sh "$B/contract" 300
# FFI-05: отказ malloc в dup() — NULL вместо SIGSEGV.
check "FFI-05 malloc failure in dup" 0 "$B/oom"
# FFI-06: figram_unload(NULL) — как free(NULL); в т.ч. после неудачной загрузки.
check "FFI-06 figram_unload(NULL)" 0 "$B/contract" null unload $D
check "FFI-06 unload(failed load)" 0 "$B/contract" null unload_failed_load $D
# P-09 (FFI-10/NP-01): рантайм Lean запускается под RLIMIT_AS — арена mimalloc заранее не резервируется
# (ffi/figram_rt.c), потоков рантайм не создаёт (ffi/figram_init.cpp, P-22). Раньше нужно было ≈2,2 ГБ
# адресов сверх занятого, а при запасе ≈ размера арены — «INTERNAL PANIC» и exit(1).
for h in 2 4 8 16 32 48 64 96 128 256 1024; do
  check_out "P-09 init with $h MiB address-space headroom → rc 0" '^figram_init=0 ' \
    env FIGRAM_TEST_AS_HEADROOM=$((h << 20)) "$B/contract" rlimit_init
done
check_out "P-09 init under RLIMIT_AS=512MB → rc 0" '^figram_init=0 ' prlimit --as=512000000 -- "$B/contract" rlimit_init
# Граница: без ≈1 МиБ свободных адресов распределитель Lean завершает процесс своим сообщением о
# нехватке памяти (код 1), как при нехватке памяти во время работы, — без сигнала и без зависания.
check_rc_out "P-09 init with 256 KiB headroom → Lean out-of-memory exit 1, no signal" 1 'INTERNAL PANIC: out of memory' \
  env FIGRAM_TEST_AS_HEADROOM=262144 "$B/contract" rlimit_init
# P-21 (FFI-03, I-17): вызовы до figram_init — ленивая потокобезопасная инициализация.
check "P-21 lazy init (load/request before figram_init)" 0 "$B/contract" lazy $D
check "P-21 lazy init race, 8 threads x 300 processes" 0 tests/capi/init_race.sh "$B/contract" 300 lazy_race
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
# P-22 (FFI-09, NP-02): диспозиции SIGPIPE, SIGSEGV, SIGBUS хоста и sigaltstack вызывающего потока
# сохраняются; встроенный рантайм не запускает потоков и не открывает дескрипторов.
check "P-22 signals preserved (defaults)" 0 "$B/contract" signals default
check "P-22 signals preserved (custom handlers + altstack)" 0 "$B/contract" signals custom
check "P-22 no runtime threads or file descriptors" 0 "$B/contract" host_state $D
# Не регрессировало: основная обвязка и нагрузка с выгрузкой других автоматов.
check "harness 16x20000 + reload" 0 "$B/harness_plain" $D memory
check "stress 16x20000 + 4 loaders" 0 "$B/stress" $D 16 20000 4
echo "регрессионных проверок C-ABI $n, провалено $nfail"
exit $fail

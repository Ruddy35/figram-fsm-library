#!/usr/bin/env bash
# Контроль чувствительности регрессионных тестов fix-ffi: каждое исправление по очереди
# «откатывается» в копии figram_ffi.c (или подменой ffi/figram_init.cpp), и соответствующий подтест
# обязан покраснеть.
#   tests/capi/mutants.sh   (после ffi/build.sh)   — код 0, если каждый мутант пойман.
set -u
cd "$(dirname "$0")/../.."
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
P=$(lean --print-prefix)
O=.lake/build/ir/Figram
OBJS=""; for m in Core Chart Compile Lint Format FormatIO Engine Api; do OBJS="$OBJS $O/$m.c.o.export"; done
D=examples/pizza.bot.json; D2=examples/features.bot.json
ok=1
# mutant ИМЯ "СТАРОЕ" "НОВОЕ" ПОДТЕСТ… — ПОДТЕСТ на мутанте обязан дать код ≠ 0. INIT_O — объект
# с lean_initialize_runtime_module (по умолчанию ffi/out/figram_init.o; пусто — штатный из libleanrt.a).
mutant() {
  local name=$1 old=$2 new=$3; shift 3
  python3 - "$old" "$new" "$T/m.c" <<'PY' || { echo "НЕТ $name: замена не применилась"; ok=0; return; }
import sys
old, new, out = sys.argv[1:4]
s = open("ffi/figram_ffi.c", encoding="utf-8").read()
if s.count(old) != 1: sys.exit(1)
open(out, "w", encoding="utf-8").write(s.replace(old, new))
PY
  cp ffi/figram.h "$T/figram.h"
  if ! leanc -c -fexceptions -o "$T/m.o" "$T/m.c" 2> "$T/cc.log" || \
     ! gcc -g -O1 -pthread -I"$P/include" -o "$T/contract" tests/capi/contract.c "$T/m.o" ffi/out/figram_guard.o ffi/out/figram_rt.o ${INIT_O-ffi/out/figram_init.o} $OBJS \
         $(cat ffi/out/LDFLAGS.runtime) 2>> "$T/cc.log"; then
    echo "НЕТ $name: мутант не собирается"; grep -m3 error "$T/cc.log"; ok=0; return
  fi
  out=$(timeout 120 "$@" 2>&1); rc=$?
  if [ $rc -ne 0 ]; then echo "ok   $name пойман: $(printf '%s\n' "$out" | grep -m1 -E '^FAIL|PANIC|terminate|figram_init=|Segmentation' | cut -c1-120)"
  else echo "НЕТ $name НЕ пойман"; ok=0; fi
}
C="$T/contract"
mutant "P-09 без системных размеров рантайма (арена mimalloc 1 ГиБ)" \
  'figram_rt_defaults();
  lean_initialize_runtime_module();' 'lean_initialize_runtime_module();' \
  "$C" host_state $D
INIT_O= mutant "P-22 штатный запуск рантайма (поток libuv, io_uring, дескрипторы)" \
  'figram_rt_defaults();' 'figram_rt_defaults();' \
  "$C" host_state $D
leanc -c -DFIGRAM_STACK_GUARD -o "$T/init_guard.o" ffi/figram_init.cpp
INIT_O="$T/init_guard.o" mutant "P-22 обработчик SIGSEGV/SIGBUS во встроенном режиме" \
  'figram_rt_defaults();' 'figram_rt_defaults();' \
  "$C" signals default
mutant "P-21 без ленивой инициализации" \
  'if (__atomic_load_n(&init_state, __ATOMIC_ACQUIRE) == 2) return 0;
  return figram_init();' 'return 0;' \
  "$C" lazy $D
mutant "P-21 без проверки NULL запроса" \
  'if (line == NULL && !(have_len && len == 0)) return err_reply("bad_request", "запрос — NULL", 0, NULL);' '' \
  "$C" null_safe $D
mutant "P-21 без обнаружения повторной выгрузки" \
  'if (!__atomic_compare_exchange_n(&s->state, &live, FG_DEAD, 0, __ATOMIC_ACQ_REL, __ATOMIC_ACQUIRE)) {' \
  'if (__atomic_store_n(&s->state, FG_DEAD, __ATOMIC_RELEASE), 0) {' \
  "$C" lifetime $D $D2
mutant "P-21 _n без проверки NUL" \
  'const char* z = have_len ? (const char*)memchr(line, 0, len) : NULL;' 'const char* z = NULL;' \
  "$C" lengths $D
mutant "P-18 без проверки UTF-8 запроса" \
  'if (!utf8_ok((const unsigned char*)line, len, &chars, &bad))
    return' 'if (!utf8_ok((const unsigned char*)line, len, &chars, &bad) && 0)
    return' \
  "$C" utf8_strict $D
mutant "P-18 без предела размера" \
  'if (max != 0 && len > max)' 'if (0)' \
  "$C" limit $D
mutant "P-22 без восстановления SIGPIPE" \
  'if (have_pipe) sigaction(SIGPIPE, pipe_old.b, (void*)0);' '' \
  "$C" signals custom
[ $ok = 1 ] && echo "все мутанты пойманы" || { echo "НЕТ: есть непойманные мутанты"; exit 1; }

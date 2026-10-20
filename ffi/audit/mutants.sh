#!/usr/bin/env bash
# Контроль чувствительности регрессионных тестов fix-ffi: каждое исправление по очереди
# «откатывается» в копии figram_ffi.c, и соответствующий подтест обязан покраснеть.
#   ffi/audit/mutants.sh   (после ffi/build.sh)   — код 0, если каждый мутант пойман.
set -u
cd "$(dirname "$0")/../.."
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
P=$(lean --print-prefix)
O=.lake/build/ir/Figram
OBJS=""; for m in Core Chart Compile Lint Format FormatIO Engine Api; do OBJS="$OBJS $O/$m.c.o.export"; done
D=examples/pizza.bot.json; D2=examples/features.bot.json
ok=1
# mutant ИМЯ "СТАРОЕ" "НОВОЕ" ПОДТЕСТ… — ПОДТЕСТ на мутанте обязан дать код ≠ 0
# (или, для rlimit_init, не напечатать «figram_init=2»).
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
     ! gcc -g -O1 -pthread -I"$P/include" -o "$T/contract" ffi/audit/contract.c "$T/m.o" ffi/out/figram_guard.o $OBJS \
         $(cat ffi/out/LDFLAGS.runtime) 2>> "$T/cc.log"; then
    echo "НЕТ $name: мутант не собирается"; grep -m3 error "$T/cc.log"; ok=0; return
  fi
  out=$(timeout 120 "$@" 2>&1); rc=$?
  if [ "$1" = prlimit ] || [ "$1" = env ]; then
    printf '%s' "$out" | grep -q '^figram_init=2 ' && rc=0 || rc=1
  fi
  if [ $rc -ne 0 ]; then echo "ok   $name пойман: $(printf '%s\n' "$out" | grep -m1 -E '^FAIL|PANIC|terminate|figram_init=|Segmentation' | cut -c1-120)"
  else echo "НЕТ $name НЕ пойман"; ok=0; fi
}
C="$T/contract"
mutant "P-09 без перехвата исключения C++" \
  'if (figram_guard_call(do_init, &r, what, sizeof what) != 0) {' 'do_init(&r); if (0) {' \
  prlimit --as=2100000000 -- "$C" rlimit_init
mutant "P-09 без проверки запаса RLIMIT_AS" \
  'if (as_headroom_short()) rc = 2;' 'if (0) rc = 2;' \
  env FIGRAM_TEST_AS_HEADROOM=1073741824 "$C" rlimit_init
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
mutant "P-22 без восстановления sigaltstack" \
  'if (have_stack) sigaltstack(stack_old.b, (void*)0);' '' \
  "$C" signals custom
[ $ok = 1 ] && echo "все мутанты пойманы" || { echo "НЕТ: есть непойманные мутанты"; exit 1; }

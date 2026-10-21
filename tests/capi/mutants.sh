#!/usr/bin/env bash
# Sensitivity control of the C ABI regression subtests: each protection of the shim in turn is
# reverted in a copy of figram_ffi.c (or by replacing ffi/figram_init.cpp), and the matching
# subtest must fail.
#   tests/capi/mutants.sh   (after ffi/build.sh)   — exit code 0 if every mutant is caught.
set -u
cd "$(dirname "$0")/../.."
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
P=$(lean --print-prefix)
O=.lake/build/ir/Figram
OBJS=""; for m in Core Chart Compile Lint Format FormatIO Engine Api; do OBJS="$OBJS $O/$m.c.o.export"; done
D=examples/pizza.bot.json; D2=examples/features.bot.json
ok=1
# mutant NAME "OLD" "NEW" SUBTEST… — SUBTEST on the mutant must exit with a code ≠ 0. INIT_O is the
# object with lean_initialize_runtime_module (default ffi/out/figram_init.o; empty — the stock one from libleanrt.a).
mutant() {
  local name=$1 old=$2 new=$3; shift 3
  python3 - "$old" "$new" "$T/m.c" <<'PY' || { echo "FAIL $name: the replacement did not apply"; ok=0; return; }
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
    echo "FAIL $name: the mutant does not build"; grep -m3 error "$T/cc.log"; ok=0; return
  fi
  out=$(timeout 120 "$@" 2>&1); rc=$?
  if [ $rc -ne 0 ]; then echo "ok   $name caught: $(printf '%s\n' "$out" | grep -m1 -E '^FAIL|PANIC|terminate|figram_init=|Segmentation' | cut -c1-120)"
  else echo "FAIL $name NOT caught"; ok=0; fi
}
C="$T/contract"
mutant "no runtime size defaults (1 GiB mimalloc arena)" \
  'figram_rt_defaults();
  lean_initialize_runtime_module();' 'lean_initialize_runtime_module();' \
  "$C" host_state $D
INIT_O= mutant "stock runtime start-up (libuv thread, io_uring, descriptors)" \
  'figram_rt_defaults();' 'figram_rt_defaults();' \
  "$C" host_state $D
leanc -c -DFIGRAM_STACK_GUARD -o "$T/init_guard.o" ffi/figram_init.cpp
INIT_O="$T/init_guard.o" mutant "SIGSEGV/SIGBUS handler in the embedded library" \
  'figram_rt_defaults();' 'figram_rt_defaults();' \
  "$C" signals default
mutant "no lazy initialization" \
  'if (__atomic_load_n(&init_state, __ATOMIC_ACQUIRE) == 2) return 0;
  return figram_init();' 'return 0;' \
  "$C" lazy $D
mutant "no NULL check of the request" \
  'if (line == NULL && !(have_len && len == 0)) return err_reply("bad_request", "request is NULL", 0, NULL);' '' \
  "$C" null_safe $D
mutant "no double-unload detection" \
  'if (!__atomic_compare_exchange_n(&s->state, &live, FG_DEAD, 0, __ATOMIC_ACQ_REL, __ATOMIC_ACQUIRE)) {' \
  'if (__atomic_store_n(&s->state, FG_DEAD, __ATOMIC_RELEASE), 0) {' \
  "$C" lifetime $D $D2
mutant "_n variants without the NUL check" \
  'const char* z = have_len ? (const char*)memchr(line, 0, len) : NULL;' 'const char* z = NULL;' \
  "$C" lengths $D
mutant "no UTF-8 check of the request" \
  'if (!utf8_ok((const unsigned char*)line, len, &chars, &bad))
    return' 'if (!utf8_ok((const unsigned char*)line, len, &chars, &bad) && 0)
    return' \
  "$C" utf8_strict $D
mutant "no request size limit" \
  'if (max != 0 && len > max)' 'if (0)' \
  "$C" limit $D
mutant "SIGPIPE not restored" \
  'if (have_pipe) sigaction(SIGPIPE, pipe_old.b, (void*)0);' '' \
  "$C" signals custom
[ $ok = 1 ] && echo "all mutants caught" || { echo "FAIL: some mutants were not caught"; exit 1; }

#!/usr/bin/env bash
# Адаптированная сборка libfigram.a для тулчейна Lean, собранного из исходников
# gcc/libstdc++ (в префиксе нет libc++.a, libc++abi.a, libuv.a, libgmp.a):
# рантайм C++ — системный libstdc++, libuv и gmp — системные статические .a.
# Затем собирает аудит-тесты ffi/audit в вариантах plain, asan (ASan+UBSan), tsan.
#   ffi/audit/build_audit.sh [OUT]   (по умолчанию ffi/audit/out)
set -euo pipefail
cd "$(dirname "$0")/../.."
OUT=${1:-ffi/audit/out}
# AUDIT TEST-ONLY (integration): ONE from-source toolchain adaptation for the whole branch.
# The library and the runtime archives come from ffi/build.sh (adapted in d8a548b: system
# static libuv/libgmp, EMPTY libc++/libc++abi stubs, C++ runtime = dynamic -lstdc++), the
# same artefacts that verify.sh and the Go build (CGO_LDFLAGS=-lstdc++) use.
bash ffi/build.sh >/dev/null
P=$(lean --print-prefix)
O=.lake/build/ir/Figram
MODS="Core Chart Compile Lint Format FormatIO Engine Api"
OBJS=""; for m in $MODS; do OBJS="$OBJS $O/$m.c.o.export"; done
mkdir -p "$OUT/lean"
if [ "$(realpath "$OUT")" != "$(realpath ffi/out)" ]; then
  cp ffi/out/figram_ffi.o ffi/out/libfigram.a "$OUT/"; cp ffi/out/lean/*.a "$OUT/lean/"
fi
LIBS="-L$OUT -l:libfigram.a -L$OUT/lean -Wl,--start-group -lInit -lleanrt -Wl,--end-group -Wl,-Bstatic -luv -lgmp -Wl,-Bdynamic -lstdc++ -lm -ldl -lpthread -lrt"
echo "$LIBS" > "$OUT/LDFLAGS"

A=ffi/audit
CF="-g -O1 -I$P/include -pthread"
gcc $CF -o "$OUT/contract" $A/contract.c $LIBS
gcc $CF -Wl,--wrap=malloc -o "$OUT/oom" $A/oom.c $LIBS
gcc $CF -o "$OUT/stress" $A/stress.c $LIBS
gcc -O1 -o "$OUT/harness_plain" ffi/c_harness.c ffi/c_harness_io.c $LIBS
# ASan+UBSan, как verify.sh: прослойка и тесты инструментированы, модули Lean — нет.
SAN="-fsanitize=address,undefined -fno-omit-frame-pointer"
gcc -c -g $SAN -I"$P/include" -o "$OUT/ffi_asan.o" ffi/figram_ffi.c
LIBS_NOFFI="-L$OUT/lean $OBJS -Wl,--start-group -lInit -lleanrt -Wl,--end-group -Wl,-Bstatic -luv -lgmp -Wl,-Bdynamic -lstdc++ -lm -ldl -lpthread -lrt"
for t in contract stress; do gcc $CF $SAN -o "$OUT/${t}_asan" $A/$t.c "$OUT/ffi_asan.o" $LIBS_NOFFI; done
gcc -g $SAN -o "$OUT/harness_asan" ffi/c_harness.c ffi/c_harness_io.c "$OUT/ffi_asan.o" $LIBS_NOFFI
# TSan: прослойка, тесты И сгенерированный C модулей Figram инструментированы
# (подсчёт ссылок lean.h инлайнится в них); рантайм Lean и Init — нет.
TS="-fsanitize=thread -g -O1"
mkdir -p "$OUT/tsan"
for m in $MODS; do gcc -c $TS -I"$P/include" -fwrapv -DNDEBUG -o "$OUT/tsan/$m.o" $O/$m.c; done
gcc -c $TS -I"$P/include" -o "$OUT/tsan/ffi.o" ffi/figram_ffi.c
TSOBJS=""; for m in $MODS; do TSOBJS="$TSOBJS $OUT/tsan/$m.o"; done
TLIBS="$OUT/tsan/ffi.o $TSOBJS -L$OUT/lean -Wl,--start-group -lInit -lleanrt -Wl,--end-group -Wl,-Bstatic -luv -lgmp -Wl,-Bdynamic -lstdc++ -lm -ldl -lpthread -lrt"
for t in contract stress; do gcc $CF $TS -o "$OUT/${t}_tsan" $A/$t.c $TLIBS; done
echo "готово: $OUT"

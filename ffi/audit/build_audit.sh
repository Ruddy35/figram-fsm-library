#!/usr/bin/env bash
# Сборка аудит-тестов ffi/audit в вариантах plain, asan (ASan+UBSan), tsan на библиотеке
# из ffi/build.sh (любой тулчейн Lean: официальный релиз или сборка из исходников —
# флаги компоновки берутся из ffi/out/LDFLAGS, B-01).
#   ffi/audit/build_audit.sh [OUT]   (по умолчанию ffi/audit/out)
set -euo pipefail
cd "$(dirname "$0")/../.."
OUT=${1:-ffi/audit/out}
bash ffi/build.sh >/dev/null
P=$(lean --print-prefix)
O=.lake/build/ir/Figram
MODS="Core Chart Compile Lint Format FormatIO Engine Api"
OBJS=""; for m in $MODS; do OBJS="$OBJS $O/$m.c.o.export"; done
mkdir -p "$OUT"
LIBS=$(cat ffi/out/LDFLAGS)
RTLIBS=$(cat ffi/out/LDFLAGS.runtime)
cp ffi/out/LDFLAGS "$OUT/LDFLAGS"

A=ffi/audit
CF="-g -O1 -I$P/include -pthread"
gcc $CF -o "$OUT/contract" $A/contract.c $LIBS
gcc $CF -Wl,--wrap=malloc -o "$OUT/oom" $A/oom.c $LIBS
gcc $CF -o "$OUT/stress" $A/stress.c $LIBS
gcc -O1 -o "$OUT/harness_plain" ffi/c_harness.c ffi/c_harness_io.c $LIBS
# ASan+UBSan, как verify.sh: прослойка и тесты инструментированы, модули Lean — нет.
SAN="-fsanitize=address,undefined -fno-omit-frame-pointer"
gcc -c -g $SAN -fexceptions -I"$P/include" -o "$OUT/ffi_asan.o" ffi/figram_ffi.c
LIBS_NOFFI="ffi/out/figram_guard.o $OBJS $RTLIBS"
for t in contract stress; do gcc $CF $SAN -o "$OUT/${t}_asan" $A/$t.c "$OUT/ffi_asan.o" $LIBS_NOFFI; done
gcc -g $SAN -o "$OUT/harness_asan" ffi/c_harness.c ffi/c_harness_io.c "$OUT/ffi_asan.o" $LIBS_NOFFI
# TSan: прослойка, тесты И сгенерированный C модулей Figram инструментированы
# (подсчёт ссылок lean.h инлайнится в них); рантайм Lean и Init — нет.
TS="-fsanitize=thread -g -O1"
mkdir -p "$OUT/tsan"
for m in $MODS; do gcc -c $TS -I"$P/include" -fwrapv -DNDEBUG -o "$OUT/tsan/$m.o" $O/$m.c; done
gcc -c $TS -fexceptions -I"$P/include" -o "$OUT/tsan/ffi.o" ffi/figram_ffi.c
g++ -c $TS -I"$P/include" -o "$OUT/tsan/guard.o" ffi/figram_guard.cpp
TSOBJS=""; for m in $MODS; do TSOBJS="$TSOBJS $OUT/tsan/$m.o"; done
TLIBS="$OUT/tsan/ffi.o $OUT/tsan/guard.o $TSOBJS $RTLIBS"
for t in contract stress; do gcc $CF $TS -o "$OUT/${t}_tsan" $A/$t.c $TLIBS; done
echo "готово: $OUT"

#!/usr/bin/env bash
# Builds the tests of tests/capi in the variants plain, asan (ASan+UBSan) and tsan against the
# library from ffi/build.sh (any Lean toolchain, official release or built from source: the
# linker flags come from ffi/out/LDFLAGS).
#   tests/capi/build.sh [OUT]   (default tests/capi/out)
set -euo pipefail
cd "$(dirname "$0")/../.."
OUT=${1:-tests/capi/out}
bash ffi/build.sh >/dev/null
P=$(lean --print-prefix)
O=.lake/build/ir/Figram
MODS="Core Chart Compile Lint Format FormatIO Engine Api"
OBJS=""; for m in $MODS; do OBJS="$OBJS $O/$m.c.o.export"; done
mkdir -p "$OUT"
LIBS=$(cat ffi/out/LDFLAGS)
RTLIBS=$(cat ffi/out/LDFLAGS.runtime)
cp ffi/out/LDFLAGS "$OUT/LDFLAGS"

A=tests/capi
CF="-g -O1 -I$P/include -pthread"
gcc $CF -o "$OUT/contract" $A/contract.c $LIBS
gcc $CF -Wl,--wrap=malloc -o "$OUT/oom" $A/oom.c $LIBS
gcc $CF -o "$OUT/stress" $A/stress.c $LIBS
gcc -O1 -o "$OUT/harness_plain" $A/c_harness.c $A/c_harness_io.c $LIBS
# ASan+UBSan, as in verify.sh: the shim and the tests are instrumented, the Lean modules are not.
SAN="-fsanitize=address,undefined -fno-omit-frame-pointer"
gcc -c -g $SAN -fexceptions -I"$P/include" -o "$OUT/ffi_asan.o" ffi/figram_ffi.c
LIBS_NOFFI="ffi/out/figram_guard.o ffi/out/figram_rt.o ffi/out/figram_init.o $OBJS $RTLIBS"
for t in contract stress; do gcc $CF $SAN -o "$OUT/${t}_asan" $A/$t.c "$OUT/ffi_asan.o" $LIBS_NOFFI; done
gcc -g $SAN -o "$OUT/harness_asan" $A/c_harness.c $A/c_harness_io.c "$OUT/ffi_asan.o" $LIBS_NOFFI
# TSan: the shim, the tests AND the generated C of the Figram modules are instrumented
# (the reference counting of lean.h is inlined into them); the Lean runtime and Init are not.
TS="-fsanitize=thread -g -O1"
mkdir -p "$OUT/tsan"
for m in $MODS; do gcc -c $TS -I"$P/include" -fwrapv -DNDEBUG -o "$OUT/tsan/$m.o" $O/$m.c; done
gcc -c $TS -fexceptions -I"$P/include" -o "$OUT/tsan/ffi.o" ffi/figram_ffi.c
g++ -c $TS -I"$P/include" -o "$OUT/tsan/guard.o" ffi/figram_guard.cpp
gcc -c $TS -I"$P/include" -o "$OUT/tsan/rt.o" ffi/figram_rt.c
g++ -c $TS -I"$P/include" -o "$OUT/tsan/init.o" ffi/figram_init.cpp
TSOBJS=""; for m in $MODS; do TSOBJS="$TSOBJS $OUT/tsan/$m.o"; done
TLIBS="$OUT/tsan/ffi.o $OUT/tsan/guard.o $OUT/tsan/rt.o $OUT/tsan/init.o $TSOBJS $RTLIBS"
for t in contract stress; do gcc $CF $TS -o "$OUT/${t}_tsan" $A/$t.c $TLIBS; done
echo "done: $OUT"

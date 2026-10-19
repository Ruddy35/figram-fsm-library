#!/usr/bin/env bash
# Адаптированная сборка libfigram.a для тулчейна Lean, собранного из исходников
# gcc/libstdc++ (в префиксе нет libc++.a, libc++abi.a, libuv.a, libgmp.a):
# рантайм C++ — системный libstdc++, libuv и gmp — системные статические .a.
# Затем собирает аудит-тесты ffi/audit в вариантах plain, asan (ASan+UBSan), tsan.
#   ffi/audit/build_audit.sh [OUT]   (по умолчанию ffi/audit/out)
set -euo pipefail
cd "$(dirname "$0")/../.."
OUT=${1:-ffi/audit/out}
lake build Figram FigramApi:static
P=$(lean --print-prefix)
O=.lake/build/ir/Figram
MODS="Core Chart Compile Lint Format FormatIO Engine Api"
OBJS=""; for m in $MODS; do OBJS="$OBJS $O/$m.c.o.export"; done
mkdir -p "$OUT/lean"
leanc -c -o "$OUT/figram_ffi.o" ffi/figram_ffi.c
rm -f "$OUT/libfigram.a"
ar rcs "$OUT/libfigram.a" "$OUT/figram_ffi.o" $OBJS
cp "$P/lib/lean/libInit.a" "$P/lib/lean/libleanrt.a" "$OUT/lean/"
SYS=/usr/lib/x86_64-linux-gnu
cp "$SYS/libuv.a" "$SYS/libgmp.a" "$OUT/lean/"
LIBS="-L$OUT -l:libfigram.a -L$OUT/lean -Wl,--start-group -lInit -lleanrt -Wl,--end-group -Wl,-Bstatic -lstdc++ -luv -lgmp -Wl,-Bdynamic -lm -ldl -lpthread -lrt"
echo "$LIBS" > "$OUT/LDFLAGS"
# Для cgo: в ffi/go-example зашиты -lc++ -lc++abi из ../out/lean; при сборке Go
# этим скриптом ffi/out — ссылка на OUT, а libc++.a/libc++abi.a — копии libstdc++.a/libsupc++.a.
GCC_LIB=$(dirname "$(gcc -print-file-name=libstdc++.a)")
cp "$GCC_LIB/libstdc++.a" "$OUT/lean/libc++.a"
cp "$GCC_LIB/libsupc++.a" "$OUT/lean/libc++abi.a"

A=ffi/audit
CF="-g -O1 -I$P/include -pthread"
gcc $CF -o "$OUT/contract" $A/contract.c $LIBS
gcc $CF -Wl,--wrap=malloc -o "$OUT/oom" $A/oom.c $LIBS
gcc $CF -o "$OUT/stress" $A/stress.c $LIBS
gcc -O1 -o "$OUT/harness_plain" ffi/c_harness.c ffi/c_harness_io.c $LIBS
# ASan+UBSan, как verify.sh: прослойка и тесты инструментированы, модули Lean — нет.
SAN="-fsanitize=address,undefined -fno-omit-frame-pointer"
gcc -c -g $SAN -I"$P/include" -o "$OUT/ffi_asan.o" ffi/figram_ffi.c
LIBS_NOFFI="-L$OUT/lean $OBJS -Wl,--start-group -lInit -lleanrt -Wl,--end-group -Wl,-Bstatic -lstdc++ -luv -lgmp -Wl,-Bdynamic -lm -ldl -lpthread -lrt"
for t in contract stress; do gcc $CF $SAN -o "$OUT/${t}_asan" $A/$t.c "$OUT/ffi_asan.o" $LIBS_NOFFI; done
gcc -g $SAN -o "$OUT/harness_asan" ffi/c_harness.c ffi/c_harness_io.c "$OUT/ffi_asan.o" $LIBS_NOFFI
# TSan: прослойка, тесты И сгенерированный C модулей Figram инструментированы
# (подсчёт ссылок lean.h инлайнится в них); рантайм Lean и Init — нет.
TS="-fsanitize=thread -g -O1"
mkdir -p "$OUT/tsan"
for m in $MODS; do gcc -c $TS -I"$P/include" -fwrapv -DNDEBUG -o "$OUT/tsan/$m.o" $O/$m.c; done
gcc -c $TS -I"$P/include" -o "$OUT/tsan/ffi.o" ffi/figram_ffi.c
TSOBJS=""; for m in $MODS; do TSOBJS="$TSOBJS $OUT/tsan/$m.o"; done
TLIBS="$OUT/tsan/ffi.o $TSOBJS -L$OUT/lean -Wl,--start-group -lInit -lleanrt -Wl,--end-group -Wl,-Bstatic -lstdc++ -luv -lgmp -Wl,-Bdynamic -lm -ldl -lpthread -lrt"
for t in contract stress; do gcc $CF $TS -o "$OUT/${t}_tsan" $A/$t.c $TLIBS; done
echo "готово: $OUT"

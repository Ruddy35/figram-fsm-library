#!/usr/bin/env bash
# Сборка встраиваемой статической библиотеки Figram из доказанного кода Lean.
#   out/libfigram.a  — для Go (cgo), C, C++
#   out/lean/        — статические части рантайма Lean для линковки
set -euo pipefail
cd "$(dirname "$0")/.."
lake build Figram FigramApi:static
P=$(lean --print-prefix)
O=.lake/build/ir/Figram
OBJS=""
for m in Core Chart Compile Lint Format FormatIO Engine Api; do OBJS="$OBJS $O/$m.c.o.export"; done
mkdir -p ffi/out/lean
leanc -c -o ffi/out/figram_ffi.o ffi/figram_ffi.c
rm -f ffi/out/libfigram.a
ar rcs ffi/out/libfigram.a ffi/out/figram_ffi.o $OBJS
cp "$P/lib/lean/libInit.a" "$P/lib/lean/libleanrt.a" ffi/out/lean/
cp "$P/lib/libc++.a" "$P/lib/libc++abi.a" "$P/lib/libuv.a" "$P/lib/libgmp.a" ffi/out/lean/
echo "готово: ffi/out/libfigram.a"

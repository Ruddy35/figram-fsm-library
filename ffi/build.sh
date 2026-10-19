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
# AUDIT TEST-ONLY ADAPTATION (Lean built from source with gcc/libstdc++): the prefix has no bundled
# libc++/libuv/libgmp archives. Use system static libuv/libgmp and empty libc++ stubs (link with -lstdc++).
if [ -f "$P/lib/libc++.a" ]; then cp "$P/lib/libc++.a" "$P/lib/libc++abi.a" "$P/lib/libuv.a" "$P/lib/libgmp.a" ffi/out/lean/
else cp /usr/lib/x86_64-linux-gnu/libuv.a /usr/lib/x86_64-linux-gnu/libgmp.a ffi/out/lean/; ar rcs ffi/out/lean/libc++.a; ar rcs ffi/out/lean/libc++abi.a; fi
echo "готово: ffi/out/libfigram.a"

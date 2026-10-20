#!/usr/bin/env bash
# Local-only environment adaptation (not part of the product): the audit toolchain is Lean 4.35.0
# built from source with gcc/libstdc++, whose prefix lacks libc++.a/libc++abi.a/libuv.a/libgmp.a that
# ffi/build.sh and verify.sh copy/link (official releases ship them). This overlay makes
# `lean --print-prefix` point to a prefix with: libc++.a = linker script INPUT(-lstdc++),
# libc++abi.a = empty archive, libuv.a/libgmp.a from /home/user/figram-audit/fresh/ffi-out/lean.
# Usage: lean-overlay-setup.sh <overlay-dir>; then `source <overlay-dir>/env.sh`.
set -euo pipefail
OV=$1; REAL=/home/user/leanprover/lean4/build/release/stage1
rm -rf "$OV"; mkdir -p "$OV/bin" "$OV/prefix/lib"
ln -s "$REAL/include" "$OV/prefix/include"; ln -s "$REAL/lib/lean" "$OV/prefix/lib/lean"; ln -s "$REAL/bin" "$OV/prefix/bin"
printf 'INPUT(-lstdc++)\n' > "$OV/prefix/lib/libc++.a"; printf '!<arch>\n' > "$OV/prefix/lib/libc++abi.a"
cp /home/user/figram-audit/fresh/ffi-out/lean/libuv.a /home/user/figram-audit/fresh/ffi-out/lean/libgmp.a "$OV/prefix/lib/"
cat > "$OV/bin/lean" <<EOS
#!/bin/sh
if [ "\$1" = "--print-prefix" ]; then echo $OV/prefix; else exec $REAL/bin/lean "\$@"; fi
EOS
chmod +x "$OV/bin/lean"
printf 'source /home/user/figram-audit/env.sh\nexport PATH=%s/bin:$PATH\nexport LEAN_SYSROOT=%s\n' "$OV" "$REAL" > "$OV/env.sh"

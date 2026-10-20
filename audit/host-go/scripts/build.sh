#!/usr/bin/env bash
# Builds the Go host binaries into audit/host-go/bin/ against the REAL libfigram.a.
#   scripts/build.sh            build bin/{fghost,fgrun,fgmem,fgprobe,fgsigprobe}
#   scripts/build.sh test ARGS  go test ARGS (e.g. -race -count=1 ./...)
# Link flags come from ffi/build.sh (ffi/out/LDFLAGS, B-01): the same command works with
# the official Lean release (libc++) and with a from-source toolchain (system libstdc++).
set -euo pipefail
HERE=$(cd "$(dirname "$0")/.." && pwd)
ROOT=$(cd "$HERE/../.." && pwd)
if [ ! -f "$ROOT/ffi/out/LDFLAGS" ] || [ "${FIGRAM_REBUILD_FFI:-0}" = 1 ]; then
  bash "$ROOT/ffi/build.sh" >/dev/null
fi
export CGO_LDFLAGS="$(cat "$ROOT/ffi/out/LDFLAGS")"
cd "$HERE"
if [ "${1:-}" = test ]; then
  shift
  exec go test "$@"
fi
mkdir -p bin
for c in fghost fgrun fgmem fgprobe fgsigprobe; do go build -o "bin/$c" "./cmd/$c"; done
echo "built: $(ls bin | tr '\n' ' ')"

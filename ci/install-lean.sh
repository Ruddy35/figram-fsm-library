#!/usr/bin/env bash
# Installs Lean from lean-toolchain straight from GitHub Releases and checks the sha256 of the
# archive against ci/lean-toolchain.sha256 (no `curl | sh`, no elan from master).
# Usage: ci/install-lean.sh [DIR]   (default ~/.lean-toolchain)
# Prints the bin directory; on GitHub Actions it also adds it to GITHUB_PATH.
# FIGRAM_LEAN_TOFU=1 — only for the first run, when no pinned checksum exists yet: the archive
# is accepted and its sha256 is written next to it (<DIR>/<archive>.sha256).
set -euo pipefail
root=$(cd "$(dirname "$0")/.." && pwd)
tc=$(tr -d ' \r\n' < "$root/lean-toolchain")
case "$tc" in
  leanprover/lean4:v*) ;;
  *) echo "unexpected lean-toolchain: $tc" >&2; exit 1 ;;
esac
ver=${tc#leanprover/lean4:v}
name="lean-$ver-linux"
dest=${1:-$HOME/.lean-toolchain}
mkdir -p "$dest"
curl -sSfL --retry 3 -o "$dest/$name.tar.zst" \
  "https://github.com/leanprover/lean4/releases/download/v$ver/$name.tar.zst"
got=$(sha256sum "$dest/$name.tar.zst" | cut -d' ' -f1)
echo "$got  $name.tar.zst" > "$dest/$name.tar.zst.sha256"
pin="$root/ci/lean-toolchain.sha256"
if [ -f "$pin" ]; then
  want=$(awk -v f="$name.tar.zst" '$2 == f { print $1 }' "$pin")
  if [ -z "$want" ]; then echo "ci/lean-toolchain.sha256 has no line for $name.tar.zst" >&2; exit 1; fi
  if [ "$got" != "$want" ]; then
    echo "sha256 $name.tar.zst = $got, but $want is pinned" >&2; exit 1
  fi
  echo "sha256 $name.tar.zst matches ci/lean-toolchain.sha256" >&2
elif [ "${FIGRAM_LEAN_TOFU:-}" = 1 ]; then
  echo "TRUST ON FIRST USE: no ci/lean-toolchain.sha256; sha256 $got written to $dest/$name.tar.zst.sha256" >&2
else
  echo "no ci/lean-toolchain.sha256: the sha256 of the Lean archive is not pinned" >&2; exit 1
fi
zstd -dc "$dest/$name.tar.zst" | tar -xf - -C "$dest"
bin="$dest/$name/bin"
test -x "$bin/lean" && test -x "$bin/lake"
if [ -n "${GITHUB_PATH:-}" ]; then echo "$bin" >> "$GITHUB_PATH"; fi
echo "$bin"

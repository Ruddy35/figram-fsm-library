#!/usr/bin/env bash
# Ставит Lean из lean-toolchain прямо с GitHub Releases и сверяет sha256 архива с
# ci/lean-toolchain.sha256 (T-15: без `curl | sh` и без elan из master).
# Использование: ci/install-lean.sh [каталог]   (по умолчанию ~/.lean-toolchain)
# Печатает каталог bin; в GitHub Actions сам добавляет его в GITHUB_PATH.
# FIGRAM_LEAN_TOFU=1 — только для первого прогона, когда закреплённой суммы ещё нет:
# архив принимается, его sha256 пишется рядом с архивом (<каталог>/<архив>.sha256).
set -euo pipefail
root=$(cd "$(dirname "$0")/.." && pwd)
tc=$(tr -d ' \r\n' < "$root/lean-toolchain")
case "$tc" in
  leanprover/lean4:v*) ;;
  *) echo "неожиданный lean-toolchain: $tc" >&2; exit 1 ;;
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
  if [ -z "$want" ]; then echo "в ci/lean-toolchain.sha256 нет строки для $name.tar.zst" >&2; exit 1; fi
  if [ "$got" != "$want" ]; then
    echo "sha256 $name.tar.zst = $got, а закреплено $want" >&2; exit 1
  fi
  echo "sha256 $name.tar.zst совпадает с ci/lean-toolchain.sha256" >&2
elif [ "${FIGRAM_LEAN_TOFU:-}" = 1 ]; then
  echo "ПЕРВОЕ ДОВЕРИЕ: ci/lean-toolchain.sha256 нет; sha256 $got записан в $dest/$name.tar.zst.sha256" >&2
else
  echo "нет ci/lean-toolchain.sha256: sha256 архива Lean не закреплён" >&2; exit 1
fi
zstd -dc "$dest/$name.tar.zst" | tar -xf - -C "$dest"
bin="$dest/$name/bin"
test -x "$bin/lean" && test -x "$bin/lake"
if [ -n "${GITHUB_PATH:-}" ]; then echo "$bin" >> "$GITHUB_PATH"; fi
echo "$bin"

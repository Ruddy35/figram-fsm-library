#!/usr/bin/env bash
# Copies the static libraries, the header and the test data of a Figram release into this module,
# or checks that the copy in the tree equals the release.
#
#   scripts/update-lib.sh VERSION   download release vVERSION, verify it with its SHA256SUMS and
#                                   replace lib/linux_amd64/*, figram.h and testdata/*
#   scripts/update-lib.sh --check   compare the tree with the release named in lib/linux_amd64/VERSION
#
# --check also compares the release's SHA256SUMS with lib/linux_amd64/SHA256SUMS.release, the copy
# taken at the update, so release assets replaced afterwards are noticed. Needs curl and network.
set -euo pipefail
cd "$(dirname "$0")/.."
REPO=${FIGRAM_REPO:-Ruddy35/figram-fsm-library}
case "${1:-}" in
  --check) check=1; version=$(cat lib/linux_amd64/VERSION);;
  [0-9]*.[0-9]*.[0-9]*) check=0; version=$1;;
  *) sed -n '2,11p' "$0"; exit 2;;
esac
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
base=https://github.com/$REPO/releases/download/v$version
cli=figram-$version-linux-x86_64.tar.gz
lib=libfigram-$version-linux-x86_64.tar.gz
for f in SHA256SUMS "$cli" "$lib"; do
  curl -fsSL --retry 3 -o "$tmp/$f" "$base/$f"
done
(cd "$tmp" && sha256sum --strict -c SHA256SUMS)
tar -C "$tmp" -xzf "$tmp/$cli"
tar -C "$tmp" -xzf "$tmp/$lib"
L=$tmp/libfigram-$version-linux-x86_64
C=$tmp/figram-$version-linux-x86_64
# file of this module:its source in the release
pairs=("figram.h:$L/include/figram.h"
       "lib/linux_amd64/THIRD_PARTY_NOTICES.md:$L/THIRD_PARTY_NOTICES.md"
       "lib/linux_amd64/SHA256SUMS.release:$tmp/SHA256SUMS")
for f in "$L"/lib/*.a; do pairs+=("lib/linux_amd64/${f##*/}:$f"); done
for f in "$C"/examples/*; do pairs+=("testdata/${f##*/}:$f"); done
if [ "$check" = 1 ]; then
  bad=0
  for p in "${pairs[@]}"; do
    if cmp -s "${p%%:*}" "${p#*:}"; then echo "ok    ${p%%:*}"; else echo "DIFF  ${p%%:*}"; bad=1; fi
  done
  for f in lib/linux_amd64/*.a testdata/*; do
    case " ${pairs[*]} " in *" $f:"*) ;; *) echo "EXTRA $f"; bad=1;; esac
  done
  grep -qx "const BundledVersion = \"$version\"" version.go || { echo "DIFF  version.go"; bad=1; }
  if [ "$bad" = 0 ]; then echo "the tree equals release v$version"; else echo "the tree differs from release v$version"; exit 1; fi
else
  rm -f lib/linux_amd64/*.a testdata/*
  for p in "${pairs[@]}"; do cp "${p#*:}" "${p%%:*}"; done
  echo "$version" > lib/linux_amd64/VERSION
  sed -i "s/^const BundledVersion = .*/const BundledVersion = \"$version\"/" version.go
  echo "updated to release v$version: run go test ./... and update CHANGELOG.md"
fi

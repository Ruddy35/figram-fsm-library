#!/usr/bin/env bash
# R01: faithful extract of verify.sh lines 3, 49, 75 (and verify-prebuilt.sh line 55).
# Shows that under `set -euo pipefail` a failing `diff`/`cmp` that is the LEFT side
# of `&&` does not abort the script: it continues and exits 0.
set -euo pipefail
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
printf 'aaaa  figram\nbbbb  figram-engine\n' > "$T/SHA256SUMS"     # "expected" hashes
printf 'x\n' > "$T/figram"; printf 'y\n' > "$T/figram-engine"      # "rebuilt" binaries (different)
echo "== hash step (verify.sh:49)"
(cd "$T" && sha256sum figram figram-engine) | diff - "$T/SHA256SUMS" && echo "ok: хеши совпадают побайтно"
echo "== golden step (verify.sh:75, verify-prebuilt.sh:55)"
printf 'one\n' > "$T/g1"; printf 'two\n' > "$T/g2"
cmp "$T/g1" "$T/g2" && echo "ok"
echo; echo "ВСЁ В ПОРЯДКЕ"

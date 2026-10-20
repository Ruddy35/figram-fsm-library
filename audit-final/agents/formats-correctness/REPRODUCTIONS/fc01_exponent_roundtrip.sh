#!/usr/bin/env bash
# FC-01: `figram fmt` prints an exponent the parser itself rejects (|exp| > 1000).
# Usage: fc01_exponent_roundtrip.sh [path/to/figram]   (default: baseline bin/figram)
F="${1:-/home/user/figram-audit-worktrees/formats-correctness/bin/figram}"
d=$(mktemp -d); trap 'rm -rf "$d"' EXIT
printf '{"figram":1,"x-ui":{"zoom":1.0e-1000},"bot":{"initial":"a","children":{"a":{}}}}' > "$d/in.json"
echo "== check original";   "$F" check "$d/in.json"; echo "rc=$?"
echo "== fmt original";     "$F" fmt "$d/in.json" > "$d/out.json"; echo "rc=$?"; grep zoom "$d/out.json"
echo "== check fmt output"; "$F" check "$d/out.json"; echo "rc=$? (expected 0: fmt output must be readable)"
echo "== text layer";       printf '1.0e-1000\n10e-1001\n' | "$F" json

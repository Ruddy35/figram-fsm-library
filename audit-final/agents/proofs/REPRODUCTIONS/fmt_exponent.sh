#!/usr/bin/env bash
# A document that passes `figram check` but whose canonical text (`figram fmt`) is
# rejected by figram itself: annotation 1.5e-1000 is stored as m=15, e=-1001 and printed
# as "15e-1001", whose exponent exceeds maxExponent=1000.
# Usage: fmt_exponent.sh [figram-binary]  (default: fresh baseline build). Exit 0 = bug reproduced.
set -u
F=${1:-/home/user/figram-audit/fresh/figram}
D=$(cd "$(dirname "$0")" && pwd)
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
echo "binary: $F"
"$F" check "$D/fmt_exponent.bot.json"; c1=$?
echo "check(original) rc=$c1"
"$F" fmt "$D/fmt_exponent.bot.json" > "$T/out.json"; c2=$?
echo "fmt rc=$c2"; cat "$T/out.json"
"$F" check "$T/out.json"; c3=$?
echo "check(fmt output) rc=$c3"
[ "$c1" -eq 0 ] && [ "$c2" -eq 0 ] && [ "$c3" -ne 0 ]

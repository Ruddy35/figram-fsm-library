#!/usr/bin/env bash
# Regression test: tests/proofs/strict_gate.sh must never rewrite
# tracked files, and the tracked theorem list must act as a pin, not as an output.
#
#   bash tests/proofs/test_strict_gate_clean.sh        (needs a built tree: lake build)
#
# 1. clean tree       — the gate passes and `git status --porcelain` is the same before and after;
# 2. stale locations  — tracked theorems.txt / AllAxioms.lean carry shifted line numbers (as after
#                       any edit of Figram/*.lean that moves a theorem): the gate passes and leaves
#                       both files byte-identical (locations are informational);
# 3. stale list       — one theorem is missing from the tracked list: the gate fails and leaves
#                       the files byte-identical (the list changes only by a deliberate commit).
# Exit 0 = all three hold. The tracked files are restored on any exit.
set -uo pipefail
cd "$(dirname "$0")/../.."
A=tests/proofs/AllAxioms.lean; N=tests/proofs/theorems.txt
B=$(mktemp -d); trap 'cp "$B/A" "$A"; cp "$B/N" "$N"; rm -rf "$B"' EXIT
cp "$A" "$B/A"; cp "$N" "$B/N"
fails=0
check() { if [ "$1" = 0 ]; then echo "ok   $2"; else echo "FAIL $2"; fails=$((fails + 1)); fi; }
sums() { sha256sum "$A" "$N"; }
gate() { bash tests/proofs/strict_gate.sh > "$B/gate.log" 2>&1; echo $?; }

# 1. clean tree
st0=$(git status --porcelain)
rc=$(gate)
st1=$(git status --porcelain)
check "$rc" "1. clean tree: gate exit code $rc"
[ "$st0" = "$st1" ]; check $? "1. git status unchanged by the gate$( [ "$st0" = "$st1" ] || printf '\n%s' "$(diff <(echo "$st0") <(echo "$st1"))")"

# 2. stale locations (line numbers +1000)
python3 -I - "$A" "$N" <<'PY'
import re, sys
a, n = sys.argv[1:3]
shift = lambda s: re.sub(r"(\.lean):(\d+)", lambda m: f"{m.group(1)}:{int(m.group(2)) + 1000}", s)
for p in (a, n):
    t = open(p, encoding="utf-8").read()
    open(p, "w", encoding="utf-8").write(shift(t))
PY
before=$(sums)
rc=$(gate)
after=$(sums)
check "$rc" "2. stale locations: gate exit code $rc (names unchanged → pass)"
[ "$before" = "$after" ]; check $? "2. tracked AllAxioms.lean / theorems.txt left byte-identical"
cp "$B/A" "$A"; cp "$B/N" "$N"

# 3. stale list (first theorem removed from both tracked files)
first=$(head -1 "$N" | cut -f1)
sed -i 1d "$N"
grep -vF "#print axioms $first " "$A" > "$B/A2"; cp "$B/A2" "$A"
before=$(sums)
rc=$(gate)
after=$(sums)
[ "$rc" != 0 ] && grep -q "theorem list differs" "$B/gate.log"
check $? "3. stale list (without $first): gate must fail with 'theorem list differs' (exit $rc)"
[ "$before" = "$after" ]; check $? "3. tracked files left byte-identical"
cp "$B/A" "$A"; cp "$B/N" "$N"

st2=$(git status --porcelain)
[ "$st0" = "$st2" ]; check $? "end: git status equals the initial one"
echo "test_strict_gate_clean: $fails failure(s)"
exit $((fails > 0))

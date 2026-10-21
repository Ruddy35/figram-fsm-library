#!/usr/bin/env bash
# The proof files of tests/proofs outside the strict gate, each with its expected outcome
# (docs/proofs.md): the glue lemmas depend only on the standard axioms, the counterexamples and the
# text round trip hold (`#guard`), OpaqueDeps elaborates, and the negative controls are REJECTED
# with exactly the number of errors stated in their header ("Expected: N errors").
#   bash tests/proofs/controls.sh   (needs a built tree: lake build)   — exit 0 if all of that holds.
set -uo pipefail
cd "$(dirname "$0")/../.."
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
fails=0
ok() { echo "ok   $*"; }
bad() { echo "FAIL $*"; fails=$((fails + 1)); }
lake build FigramAudit > "$T/build.log" 2>&1 || { tail -20 "$T/build.log"; echo "FAIL lake build FigramAudit"; exit 1; }
run() { lake env lean "tests/proofs/$1.lean" > "$T/$1.out" 2>&1; echo $?; }

for f in Counterexamples TextRoundTrip OpaqueDeps; do
  rc=$(run $f)
  if [ "$rc" = 0 ] && ! grep -q ': error' "$T/$f.out"; then ok "$f.lean: exit 0"
  else bad "$f.lean: exit $rc"; head -20 "$T/$f.out" | sed 's/^/    /'; fi
done

rc=$(run GlueAxioms)
if msg=$(python3 -I - "$T/GlueAxioms.out" tests/proofs/GlueAxioms.lean "$rc" <<'PY'
import re, sys
out, src, rc = open(sys.argv[1], encoding="utf-8").read(), open(sys.argv[2], encoding="utf-8").read(), sys.argv[3]
listed = re.findall(r"^#print axioms (\S+)$", src, re.M)
ok = {"propext", "Quot.sound", "Classical.choice"}
seen = {}
for name, ax in re.findall(r"^'([^']+)' (?:depends on axioms: \[([^\]]*)\]|does not depend on any axioms)$", out, re.M):
    seen[name] = {a.strip() for a in ax.split(",") if a.strip()}
bad = [n for n in listed if n not in seen] + [f"{n}: {sorted(seen[n] - ok)}" for n in listed if n in seen and seen[n] - ok]
if rc != "0" or not listed or bad:
    print(f"exit {rc}, {len(listed)} theorems, problems: {bad}")
    sys.exit(1)
print(f"{len(listed)} glue theorems, only {sorted(ok)}")
PY
); then ok "GlueAxioms.lean: $msg"; else bad "GlueAxioms.lean: $msg"; fi

for f in CounterexamplesNegative StatementsNegative; do
  want=$(grep -m1 -oE 'Expected: [0-9]+ errors' "tests/proofs/$f.lean" | grep -oE '[0-9]+')
  rc=$(run $f)
  n=$(grep -c ': error' "$T/$f.out")
  if [ -n "$want" ] && [ "$rc" = 1 ] && [ "$n" = "$want" ]; then ok "$f.lean: rejected with $n errors, as expected"
  else bad "$f.lean: exit $rc and $n errors, expected exit 1 and ${want:-?} errors"; head -20 "$T/$f.out" | sed 's/^/    /'; fi
done

echo "proof controls: $fails failure(s)"
[ "$fails" = 0 ]

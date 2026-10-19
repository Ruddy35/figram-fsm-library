#!/usr/bin/env bash
# Strict proof gate (audit proposal; replaces verify.sh step "Теоремы без sorry",
# which only greps the word "sorry" in #print axioms of 14 hand-picked theorems).
#  1. theorem list is generated from the sources (gen_axioms.py), never empty;
#  2. #print axioms for EVERY theorem; each line must name only propext/Quot.sound/
#     Classical.choice (sorryAx, Lean.ofReduceBool, Lean.trustCompiler, custom axioms → red);
#     number of answered lines must equal the number of theorems;
#  3. AxiomGate.lean: same check from the environment (all theorems in Figram.* modules,
#     all defs/opaques, no `axiom` declarations, source list ⊆ environment);
#  4. Statements.lean: statements of the README/AUDIT theorems are pinned;
#  5. leanchecker: every declaration of every Figram.* module is re-added through the kernel
#     (catches environment hacking that #print axioms cannot see, e.g. addDeclCore doCheck:=false).
set -euo pipefail
cd "$(dirname "$0")/../.."
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
python3 -I audit/proofs/gen_axioms.py . audit/proofs/AllAxioms.lean audit/proofs/theorems.txt
n=$(wc -l < audit/proofs/theorems.txt)
lake env lean audit/proofs/AllAxioms.lean > "$T/ax.log" 2>&1
python3 -I - "$T/ax.log" "$n" <<'PY'
import re, sys
log, n = open(sys.argv[1], encoding="utf-8").read(), int(sys.argv[2])
ok = {"propext", "Quot.sound", "Classical.choice"}
lines = re.findall(r"^'([^']+)' (depends on axioms: \[([^\]]*)\]|does not depend on any axioms)", log, re.M)
bad = [(name, ax) for name, _, ax in lines if ax and not set(a.strip() for a in ax.split(",")) <= ok]
if len(lines) != n:
    sys.exit(f"STRICT GATE: expected {n} '#print axioms' answers, got {len(lines)}\n{log[-2000:]}")
for name, ax in bad:
    print(f"NON-STANDARD AXIOMS: {name}: [{ax}]")
if bad:
    sys.exit(f"STRICT GATE: {len(bad)} theorems use non-standard axioms")
print(f"ok: {n} theorems, only propext/Quot.sound/Classical.choice")
PY
lake env lean audit/proofs/AxiomGate.lean
lake env lean audit/proofs/Statements.lean > /dev/null
lake env leanchecker Figram
echo "STRICT GATE: PASS"

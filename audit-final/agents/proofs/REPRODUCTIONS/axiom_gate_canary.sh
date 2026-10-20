#!/usr/bin/env bash
# Sensitivity test of the proof gates. Works on COPIES of the worktree (never modifies it).
#  Canary A: Machine.step_guard_local "proved" by a custom axiom `cheat : ∀ p, p`, and
#            Machine.step_state_valid uses native_decide (axiom Lean.ofReduceBool).
#            Expected: verify.sh build step PASS (no warnings), verify.sh axiom step PASS (fooled),
#                      strict gate (audit/proofs/strict_gate.sh) FAIL.
#  Canary B: a theorem of `False` is added to the environment WITHOUT kernel check
#            (addDeclCore doCheck:=false, as in Lean's own leanchecker tests) and used to prove 1 = 2.
#            Expected: #print axioms shows NO axioms (so any axiom-only gate passes),
#                      leanchecker FAILS.
# Usage: [VERIFY_REV=<git rev>] axiom_gate_canary.sh [worktree]   Exit 0 iff all expectations hold.
#   VERIFY_REV=d69bbaf  → use the BASELINE verify.sh in the copies (shows the old gate is fooled);
#   default             → use the tree's verify.sh (with audit fix: strict gate, must be red).
set -u
WT=${1:-/home/user/figram-audit-worktrees/proofs}
source /home/user/figram-audit/env.sh
SCR=/tmp/claude-0/-home-user-figram-fsm-library/57129652-0dc1-5f90-93b6-ee8cd24dbb93/scratchpad
mkdir -p "$SCR"
T=$(mktemp -d "$SCR/canary.XXXX")
ok=1
expect() { if [ "$1" = "$2" ]; then echo "  EXPECTED: $3"; else echo "  UNEXPECTED: $3 (got $1, want $2)"; ok=0; fi; }

vsel() { if [ -n "${VERIFY_REV:-}" ]; then git -C "$WT" show "$VERIFY_REV:verify.sh" > verify.sh; echo "verify.sh from $VERIFY_REV"; else echo "verify.sh from tree"; fi; }
runvstep() {
  awk '/^step "Теоремы/{f=1} f&&/^step "/&&!/^step "Теоремы/{exit} f' verify.sh > vstep.body
  { echo 'set -euo pipefail; T=$(mktemp -d); LOGS=$(pwd)/verify-logs; mkdir -p "$LOGS"'
    echo 'step() { echo "== $*"; }'
    sed -n '/^logged() {/,/^}/p' verify.sh
    cat vstep.body; } > vstep.sh
  bash vstep.sh > vstep.log 2>&1; vrc=$?
  grep -E "hacked|step_guard_local|step_state_valid|STRICT|ОТКАЗ|leanchecker found" vstep.log | head -8
  if grep -q strict_gate vstep.body; then
    [ $vrc -ne 0 ] && expect red red "verify.sh proof step (strict gate) is red (exit $vrc)" || expect green red "verify.sh proof step (strict gate)"
  else
    [ $vrc -eq 0 ] && expect pass pass "baseline verify.sh axiom step passes (gate fooled)" || expect fail pass "baseline verify.sh axiom step"
  fi
}

echo "=== Canary A: custom axiom + native_decide"
cp -a "$WT" "$T/a"; cd "$T/a"; vsel
[ -d .lake ] || cp -a /home/user/figram-audit/fresh/lake-cache .lake
python3 -I - <<'PY'
import pathlib
p = pathlib.Path("Figram/CoreProofs.lean")
s = p.read_text()
s = s.replace("namespace Figram\n", "namespace Figram\n\naxiom cheat : ∀ p : Prop, p\n", 1)
i = s.index("theorem step_guard_local"); j = s.index(":= by", i); k = s.index("end Machine", j)
s = s[:j] + ":= by\n  have _ := h\n  exact cheat _\n\n" + s[k:]
s = s.replace("(m.step s ev env).1.state ∈ m.states := by\n",
              "(m.step s ev env).1.state ∈ m.states := by\n  have _h : (2 : Nat) + 2 = 4 := by native_decide\n", 1)
p.write_text(s)
PY
git diff --stat -- Figram/CoreProofs.lean | cat
lake build > build.log 2>&1; rc=$?; tail -1 build.log
if [ $rc -eq 0 ] && ! grep -q warning build.log; then expect pass pass "verify.sh build step passes (no warnings)"; else expect fail pass "verify.sh build step"; fi
runvstep
bash audit/proofs/strict_gate.sh > gate.log 2>&1; rc=$?
grep -E "NON-STANDARD|uses non-standard|declares axiom|STRICT" gate.log | head -12
[ $rc -ne 0 ] && expect red red "strict gate is red (exit $rc)" || expect green red "strict gate"

echo "=== Canary B: unchecked declaration (environment hack)"
cp -a "$WT" "$T/b"; cd "$T/b"; vsel
[ -d .lake ] || cp -a /home/user/figram-audit/fresh/lake-cache .lake
cat > Figram/HackCanary.lean <<'LEAN'
import Lean.Elab.Term

open Lean in
run_elab
  modifyEnv fun env => Id.run do
    let decl := .thmDecl { name := `Figram.hackFalse, levelParams := [], type := .const ``False [], value := .const ``False [] }
    let .ok env := env.addDeclCore (doCheck := false) 0 0 decl none |
      let _ : Inhabited Environment := ⟨env⟩
      unreachable!
    env

theorem Figram.hacked : (1 : Nat) = 2 := Figram.hackFalse.elim
LEAN
echo "import Figram.HackCanary" >> Figram.lean
lake build > build.log 2>&1; rc=$?; tail -1 build.log
if [ $rc -eq 0 ] && ! grep -q warning build.log; then expect pass pass "build passes (no warnings)"; else expect fail pass "build"; fi
printf 'import Figram\n#print axioms Figram.hacked\n' > hx.lean
lake env lean hx.lean | tee hx.log
grep -q "does not depend on any axioms" hx.log && expect none none "#print axioms sees no axioms for a proof of 1 = 2" || expect some none "#print axioms"
python3 -I audit/proofs/gen_axioms.py . audit/proofs/AllAxioms.lean audit/proofs/theorems.txt >/dev/null
lake env lean audit/proofs/AxiomGate.lean > axgate.log 2>&1; rc=$?
grep "GATE" axgate.log
[ $rc -eq 0 ] && expect green green "axiom-only gate (AxiomGate.lean) does NOT catch it" || expect red green "AxiomGate.lean"
runvstep
lake env leanchecker Figram > lc.log 2>&1; rc=$?
head -3 lc.log
[ $rc -ne 0 ] && expect red red "leanchecker is red (exit $rc)" || expect green red "leanchecker"
cd /; rm -rf "$T"
[ $ok -eq 1 ] && echo "ALL EXPECTATIONS MET" || echo "SOME EXPECTATIONS FAILED"
[ $ok -eq 1 ]

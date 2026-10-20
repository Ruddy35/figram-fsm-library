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
# The gate never writes tracked files: the list and AllAxioms.lean are generated into a temporary
# directory. The tracked audit/proofs/theorems.txt and AllAxioms.lean are a pin: their set of theorem
# NAMES must equal the generated one (adding, removing or renaming a theorem is a deliberate commit:
#   python3 -I audit/proofs/gen_axioms.py . audit/proofs/AllAxioms.lean audit/proofs/theorems.txt).
# File:line locations in them are informational and may lag behind the sources.
# Regression test: audit/proofs/test_strict_gate_clean.sh.
set -euo pipefail
cd "$(dirname "$0")/../.."
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
# 0. static: constructs that bypass or weaken checking must not appear in project sources
if grep -nE '\bsorry\b|\badmit\b|^[[:space:]]*(private |protected )?axiom\b|native_decide|ofReduceBool|ofReduceNat|trustCompiler|debug\.skipKernelTC|\brun_elab\b|\brun_cmd\b|\brun_meta\b|modifyEnv|addDeclCore|\bunsafe\b|@\[csimp\]' \
     Figram/*.lean Figram.lean Main.lean EngineMain.lean; then
  echo "STRICT GATE: forbidden construct in sources (see lines above)"; exit 1
fi
python3 -I audit/proofs/gen_axioms.py . "$T/AllAxioms.lean" "$T/theorems.txt"
python3 -I - audit/proofs/theorems.txt audit/proofs/AllAxioms.lean "$T/theorems.txt" <<'PY'
import collections, re, sys
read = lambda p: open(p, encoding="utf-8").read()
names_txt = lambda p: [l.split("\t")[0] for l in read(p).splitlines() if l.strip()]
names_ax = lambda p: re.findall(r"^#print axioms (\S+)", read(p), re.M)
tracked, tracked_ax, gen = (collections.Counter(names_txt(sys.argv[1])),
                            collections.Counter(names_ax(sys.argv[2])),
                            collections.Counter(names_txt(sys.argv[3])))
bad = []
if tracked_ax != tracked:
    bad.append("tracked AllAxioms.lean and theorems.txt disagree: "
               f"{sorted((tracked_ax - tracked) + (tracked - tracked_ax))}")
if tracked != gen:
    bad.append("theorem list differs from the tracked audit/proofs/theorems.txt: "
               f"new {sorted(gen - tracked)}, gone {sorted(tracked - gen)}")
if bad:
    print("\n".join(f"STRICT GATE: {b}" for b in bad))
    sys.exit("STRICT GATE: review the change and update the tracked list deliberately:\n"
             "  python3 -I audit/proofs/gen_axioms.py . audit/proofs/AllAxioms.lean audit/proofs/theorems.txt")
moved = len(set(read(sys.argv[1]).splitlines()) - set(read(sys.argv[3]).splitlines()))
print(f"theorem list = tracked list ({sum(gen.values())} names)"
      + (f"; {moved} locations differ (informational, tracked files are not rewritten)" if moved else ""))
PY
n=$(wc -l < "$T/theorems.txt")
lake env lean "$T/AllAxioms.lean" > "$T/ax.log" 2>&1
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

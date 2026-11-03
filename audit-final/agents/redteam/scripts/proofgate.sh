#!/usr/bin/env bash
# proofgate.sh NAME: apply DEFECTS/NAME.edit.py (no stubs), lake build (must succeed, no sorry), then the
# pinned-theorem steps of verify.sh (theorems.lean axioms + statements vs THEOREMS.expected, the ban on
# sorry/axiom...), then restore the sources and rebuild the base.
set -u
NAME=$1; R=/home/user/figram-audit/agents5/redteam; W=/home/user/figram-audit-worktrees/a5-redteam
cd $W; source /home/user/figram-audit/env.sh; export LEAN_CC=/home/user/figram-audit/fixes/memory/cc-quiet.sh
[ -z "$(git status --porcelain --untracked-files=no)" ] || { echo "tree not clean"; exit 2; }
python3 - "$R/DEFECTS/$NAME.edit.py" <<'PY'
import sys
ns = {}; exec(open(sys.argv[1]).read(), ns)
for f, old, new in ns["EDITS"]:
    s = open(f).read(); assert s.count(old) == 1, (f, old[:60]); open(f, "w").write(s.replace(old, new))
PY
git diff > $R/DEFECTS/$NAME.patch
T=$(mktemp -d)
nice -n 10 timeout 1500 lake build > $T/build.log 2>&1; echo "lake build exit $?: $(tail -1 $T/build.log)"
grep -ciE '(^|[[:space:]:])warning:|^⚠' $T/build.log | sed 's/^/build warnings: /'
grep -E "error" $T/build.log | head -5
lake env lean tests/proofs/theorems.lean > $T/ax.log 2>&1; echo "theorems.lean exit $?"
grep '^STMT ' $T/ax.log > $T/st.txt; diff -q tests/proofs/THEOREMS.expected $T/st.txt && echo "pinned statements: identical ($(wc -l < $T/st.txt))"
grep -v '^STMT ' $T/ax.log | grep -v "depends on axioms: \[propext, Classical.choice, Quot.sound\]\|depends on axioms: \[propext\]\|depends on axioms: \[propext, Quot.sound\]\|does not depend on any axioms\|depends on axioms: \[Quot.sound\]\|depends on axioms: \[propext, Classical.choice\]\|depends on axioms: \[Classical.choice, Quot.sound\]\|depends on axioms: \[Classical.choice\]" | head -5
grep -nE '\bsorry\b|\badmit\b|native_decide|^\s*axiom\s' Figram.lean Main.lean EngineMain.lean Figram/*.lean | head -3 || true
echo "forbidden-construct grep done"
cp .lake/build/bin/figram-engine $T/figram-engine-$NAME 2>/dev/null
git checkout -- Figram Main.lean EngineMain.lean
echo "restored: $(git status --porcelain --untracked-files=no | wc -l) modified"
rm -rf $T

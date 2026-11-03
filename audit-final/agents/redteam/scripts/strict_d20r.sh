#!/usr/bin/env bash
# Apply defect d20r (with repaired helper proofs), build, run tests/proofs/strict_gate.sh, restore.
set -u
R=/home/user/figram-audit/agents5/redteam; W=/home/user/figram-audit-worktrees/a5-redteam
cd $W; source /home/user/figram-audit/env.sh; export LEAN_CC=/home/user/figram-audit/fixes/memory/cc-quiet.sh
[ -z "$(git status --porcelain --untracked-files=no)" ] || { echo "tree not clean"; exit 2; }
git apply $R/DEFECTS/d20r-stay-consumes-all-timers-repaired.patch || exit 2
git diff --stat
nice -n 10 timeout 1500 lake build 2>&1 | tail -1
nice -n 10 timeout 900 bash tests/proofs/strict_gate.sh 2>&1 | tail -8; echo "strict gate exit ${PIPESTATUS[0]}"
git checkout -- Figram
echo "restored: $(git status --porcelain --untracked-files=no | wc -l) modified"

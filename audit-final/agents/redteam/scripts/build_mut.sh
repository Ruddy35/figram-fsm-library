#!/usr/bin/env bash
# build_mut.sh NAME [TARGET...]: build the mutated worktree, save binaries to bin/mut/NAME, save the patch.
set -u
NAME=$1; shift
TARGETS=${*:-figram-engine}
R=/home/user/figram-audit/agents5/redteam
W=/home/user/figram-audit-worktrees/a5-redteam
cd "$W"
source /home/user/figram-audit/env.sh
export LEAN_CC=/home/user/figram-audit/fixes/memory/cc-quiet.sh
git diff > "$R/DEFECTS/$NAME.patch"
/home/user/figram-audit/run.sh "$R/LOGS/build-$NAME" nice -n 10 timeout 1500 lake build $TARGETS
rc=$?
mkdir -p "$R/bin/mut/$NAME"
for t in $TARGETS; do cp ".lake/build/bin/$t" "$R/bin/mut/$NAME/$t" 2>/dev/null; done
grep -E "error|sorry" "$R/LOGS/build-$NAME.stdout" | head -30
exit $rc

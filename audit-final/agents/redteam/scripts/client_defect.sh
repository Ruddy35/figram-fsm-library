#!/usr/bin/env bash
# client_defect.sh NAME: inject a client defect (no build), run the client-facing suites, restore.
set -u
NAME=$1; R=/home/user/figram-audit/agents5/redteam; W=/home/user/figram-audit-worktrees/a5-redteam
cd $W
[ -z "$(git status --porcelain --untracked-files=no)" ] || { echo "tree not clean"; exit 2; }
python3 - "$R/DEFECTS/$NAME.edit.py" <<'PY'
import sys
ns = {}; exec(open(sys.argv[1]).read(), ns)
for f, old, new in ns["EDITS"]:
    s = open(f).read(); assert s.count(old) == 1, f; open(f, "w").write(s.replace(old, new))
PY
git diff > $R/DEFECTS/$NAME.patch
$R/scripts/suites.sh $NAME client host edges selftest conf-golden load
git checkout -- clients
echo "restored: $(git status --porcelain --untracked-files=no | wc -l) modified"

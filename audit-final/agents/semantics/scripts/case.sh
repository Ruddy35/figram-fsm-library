#!/usr/bin/env bash
# case.sh DOC NAME : run figram-engine DOC on cases/NAME.req.jsonl, compare with cases/NAME.expected.jsonl
set -u
R=/home/user/figram-audit/agents5/semantics/REPRODUCTIONS
B=/home/user/figram-audit-worktrees/a5-semantics/.lake/build/bin
doc="$1"; name="$2"
timeout 20 "$B/figram-engine" "$R/docs/$doc" < "$R/cases/$name.req.jsonl" > "$R/cases/$name.actual.jsonl"
rc=$?
echo "engine exit $rc"
python3 -I /home/user/figram-audit/agents5/semantics/scripts/cmp.py "$R/cases/$name.actual.jsonl" "$R/cases/$name.expected.jsonl"

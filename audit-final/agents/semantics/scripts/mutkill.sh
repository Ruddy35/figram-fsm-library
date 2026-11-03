#!/usr/bin/env bash
# mutkill.sh DOC CASE : run every mutant of DOC on cases/CASE.req.jsonl, report which ones my expectations kill
R=/home/user/figram-audit/agents5/semantics/REPRODUCTIONS
B=/home/user/figram-audit-worktrees/a5-semantics/.lake/build/bin
doc=$1; case=$2; killed=0; alive=0; alive_list=""
while read -r m; do
  FIGRAM_ALLOW_MUTANTS=1 timeout 20 "$B/figram-engine" --mutate "$m" "$R/docs/$doc.bot.json" < "$R/cases/$case.req.jsonl" > /tmp/mut.$$ 2>/dev/null
  if python3 -I /home/user/figram-audit/agents5/semantics/scripts/cmp.py /tmp/mut.$$ "$R/cases/$case.expected.jsonl" > /dev/null; then alive=$((alive+1)); alive_list="$alive_list $m"; else killed=$((killed+1)); fi
done < <(FIGRAM_ALLOW_MUTANTS=1 "$B/figram-engine" --mutants "$R/docs/$doc.bot.json")
rm -f /tmp/mut.$$
echo "$doc/$case: killed $killed alive $alive"
echo "alive:$alive_list"

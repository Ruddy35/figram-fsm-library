#!/usr/bin/env bash
# Crash matrix: one run per crash boundary (crash probability per visit of that boundary), all
# with duplicated deliveries; each must be green. Then all boundaries together with reloads.
set -u
O=/home/user/figram-audit/agents6/hostgo
H=$O/host/bin/hostgo
DB=/tmp/claude-0/-home-user/57129652-0dc1-5f90-93b6-ee8cd24dbb93/scratchpad/matrix.db
declare -A P=( [ingest_before_commit]=0.05 [ingest_after_commit]=0.05 [step_before_commit]=0.03 [step_after_commit]=0.03
  [exec_before_external]=0.05 [exec_after_external]=0.05 [exec_after_record]=0.05 [bg_before_external]=0.05
  [bg_after_external]=0.05 [bg_after_record]=0.05 [sched_before_enqueue]=0.05 [sched_after_enqueue]=0.05 [reload_after_load]=0.03 )  # 0.2 starved 300 ms background calls (LOGS/matrix-reload_after_load-p0.2-starved.log)
for p in "${!P[@]}"; do
  extra=""
  [ "$p" = reload_after_load ] && extra="-reload 20ms"
  /home/user/figram-audit/run.sh $O/LOGS/matrix-$p nice -n 10 timeout 600 $H run -users 20 -goals 2 -max-actions 40 -seed 11 \
    -db $DB -crash ${P[$p]} -crash-points $p -notify-latency 300ms -drain 60s $extra -out $O/results/matrix-$p.json
done

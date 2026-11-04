#!/usr/bin/env bash
# Negative controls: each broken host (or broken engine through a reply filter) must make an
# invariant fail (exit code 1). The same scenario with the real host is green (scripts/green.sh).
set -u
O=/home/user/figram-audit/agents6/hostgo
H=$O/host/bin/hostgo
DB=/tmp/claude-0/-home-user/57129652-0dc1-5f90-93b6-ee8cd24dbb93/scratchpad/controls.db
run() { name=$1; shift
  /home/user/figram-audit/run.sh $O/LOGS/control-$name nice -n 10 timeout 600 $H run -users 20 -goals 2 -max-actions 40 -seed 21 \
    -db $DB -notify-latency 300ms -drain 30s -reply-wait 5s -out $O/results/control-$name.json "$@"; }
run noidem -control noidem -crash 0.03 -crash-points exec_after_external,bg_after_external
run norecon -control norecon
run nobarrier -control nobarrier -exec-delay 3ms
run inlinebg -control inlinebg
run outcome-separate -control outcome-separate -crash 0.05 -crash-points exec_between_mark_and_outcome,bg_between_mark_and_outcome
run nolock -control nolock
run engine-pending-dropped -control pending-dropped
run engine-call-no-token -control call-no-token
run validator-detached-false -control detached-false

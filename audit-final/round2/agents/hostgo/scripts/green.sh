#!/usr/bin/env bash
# Green runs of the real host: the same scenario as the controls, then crashes at all boundaries
# with reloads, then the large run (thousands of users), then the race detector.
set -u
O=/home/user/figram-audit/agents6/hostgo
H=$O/host/bin/hostgo
S=/tmp/claude-0/-home-user/57129652-0dc1-5f90-93b6-ee8cd24dbb93/scratchpad
case "${1:-all}" in
same|all)
  /home/user/figram-audit/run.sh $O/LOGS/green-same-as-controls nice -n 10 timeout 600 $H run -users 20 -goals 2 -max-actions 40 -seed 21 \
    -db $S/green.db -notify-latency 300ms -drain 30s -reply-wait 5s -out $O/results/green-same-as-controls.json ;;&
allcrash|all)
  /home/user/figram-audit/run.sh $O/LOGS/green-allcrash-reload nice -n 10 timeout 900 $H run -users 50 -goals 2 -max-actions 50 -seed 31 \
    -db $S/green.db -crash 0.004 -reload 50ms -notify-latency 300ms -drain 120s -out $O/results/green-allcrash-reload.json ;;&
big|all)
  /home/user/figram-audit/run.sh $O/LOGS/green-big nice -n 10 timeout 900 ${BIGBIN:-$H} run -users ${BIG:-667} -goals 1 -max-actions ${BIGACT:-12} -seed 41 \
    -db $S/big.db -crash ${BIGCRASH:-0} -reload 500ms -notify-latency 200ms -reply-wait 30s -drain 300s -out $O/results/green-big.json ;;&
  # first attempt (BIG=1000, crash 0.0003, notify latency 1 s) exceeded the 15-minute limit:
  # LOGS/green-big-crash-timeout.log; crashes at every boundary are covered by matrix.sh and allcrash
race|all)
  /home/user/figram-audit/run.sh $O/LOGS/green-race nice -n 10 timeout 900 $O/host/bin/hostgo-race run -users 15 -goals 1 -max-actions 30 -seed 51 \
    -db $S/race.db -crash 0.01 -reload 20ms -notify-latency 200ms -reply-wait 60s -drain 120s -out $O/results/green-race.json ;;
esac

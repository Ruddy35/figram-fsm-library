#!/usr/bin/env bash
# fix/final: performance re-measurement for README «После оптимизации» (same scripts as fix-core,
# fix-protocol, host-perf). All runs nice -n 10, logged with run.sh.
set -u
source /home/user/figram-audit/env.sh
W=/home/user/figram-audit-worktrees/fix-final
B=/home/user/figram-audit/fixes/final/bin-final
BASE=/home/user/figram-audit/fixes/protocol/base-bin
L=/home/user/figram-audit/fixes/final/LOGS/perf
R=/home/user/figram-audit/run.sh
P=/tmp/claude-0/-home-user-figram-fsm-library/57129652-0dc1-5f90-93b6-ee8cd24dbb93/scratchpad/perf
mkdir -p $L
cd $W/audit/core-compiler
nice -n 10 $R $L/01-perf_scale-thresholds python3 -B perf_scale.py --engine $B/figram-engine --figram $B/figram --label final --check --only states3000,unique1000,hist_100x20,depth64,global_big --steps 2000 --load-timeout 400 --max-load-s 5 --max-step-us 1000 --out $L/01-perf_scale-thresholds.jsonl
nice -n 10 $R $L/02-perf_scale-extra python3 -B perf_scale.py --engine $B/figram-engine --figram $B/figram --label final --check --only states1000,random1000,unique2000,hist_200x20,flat_4000,deep_100_x_500leaves_hist,resume100,global300 --steps 2000 --out $L/02-perf_scale-extra.jsonl
cd $W
nice -n 10 $R $L/03-parser_scaling python3 -B tests/formats/f_parser_scaling.py --check --figram $B/figram --engine $B/figram-engine --json $L/03-parser_scaling.json --tmp /tmp/claude-0/-home-user-figram-fsm-library/57129652-0dc1-5f90-93b6-ee8cd24dbb93/scratchpad
for i in 1 2 3; do
  nice -n 10 $R $L/04-pizza-bench-$i python3 -B $P/bench_engine.py $B/figram-engine examples/pizza.bot.json --steps 50000 --seed $i --reqs /tmp/claude-0/-home-user-figram-fsm-library/57129652-0dc1-5f90-93b6-ee8cd24dbb93/scratchpad/perf/reqs$i.jsonl --label final
  nice -n 10 $R $L/05-pizza-replay-$i bash $P/replay.sh $B/figram-engine examples/pizza.bot.json /tmp/claude-0/-home-user-figram-fsm-library/57129652-0dc1-5f90-93b6-ee8cd24dbb93/scratchpad/perf/reqs$i.jsonl /tmp/claude-0/-home-user-figram-fsm-library/57129652-0dc1-5f90-93b6-ee8cd24dbb93/scratchpad/perf/out$i.jsonl
  nice -n 10 $R $L/05-pizza-replay-base-$i bash $P/replay.sh $BASE/figram-engine examples/pizza.bot.json /tmp/claude-0/-home-user-figram-fsm-library/57129652-0dc1-5f90-93b6-ee8cd24dbb93/scratchpad/perf/reqs$i.jsonl /tmp/claude-0/-home-user-figram-fsm-library/57129652-0dc1-5f90-93b6-ee8cd24dbb93/scratchpad/perf/outb$i.jsonl
done
echo PERF-DONE

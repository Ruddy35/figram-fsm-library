#!/usr/bin/env bash
# Final gate of fix/core on the final commit (all steps logged with run.sh)
set -u
source /home/user/figram-audit/env.sh
W=/home/user/figram-audit-worktrees/fix-core
L=/home/user/figram-audit/fixes/core/LOGS/final
F=/home/user/figram-audit/fixes/core/final-bin
R=/home/user/figram-audit/run.sh
T=/home/user/figram-audit/fixes/core/verify-sums
cd $W
echo "commit $(git rev-parse HEAD)" > $L/COMMIT
CGO_LDFLAGS=-lstdc++ FIGRAM_SHA256SUMS=$T/SUMS nice -n 10 $R $L/01-verify timeout 2400 ./verify.sh
nice -n 10 $R $L/02-strict_gate timeout 1500 bash audit/proofs/strict_gate.sh
git checkout -- audit/proofs/AllAxioms.lean audit/proofs/theorems.txt 2>/dev/null
cd $W/audit/core-compiler
mkdir -p $L/cc
nice -n 10 $R $L/03-cc-run_all env FIGRAM_ALLOW_MUTANTS=1 sh ./run_all.sh $F/figram-engine $F/figram $L/cc
nice -n 10 $R $L/04-perf-GREEN python3 -B perf_scale.py --engine $F/figram-engine --figram $F/figram --label final --check --only states3000,unique1000,hist_100x20,depth64,global_big --steps 2000 --load-timeout 400 --max-load-s 5 --max-step-us 1000 --out $L/04-perf-GREEN.jsonl
nice -n 10 $R $L/05-perf-extra python3 -B perf_scale.py --engine $F/figram-engine --figram $F/figram --label final --check --only states1000,random1000,unique2000,hist_200x20,flat_4000,deep_100_x_500leaves_hist,resume100,global300 --steps 2000 --out $L/05-perf-extra.jsonl
nice -n 10 $R $L/06-lint_warnings python3 -B lint_warnings.py --figram $F/figram
nice -n 10 $R $L/07-check_messages-fc07 python3 -B check_messages_diff.py --old /home/user/figram-audit/fixes/core/base-bin/figram --new $F/figram --docs 20 --fc07
nice -n 10 $R $L/08-compile_same python3 -B compile_same.py --old-figram /home/user/figram-audit/fixes/core/base-bin/figram --old-engine /home/user/figram-audit/fixes/core/base-bin/figram-engine --new-figram $F/figram --new-engine $F/figram-engine --docs 60
cd $W/audit/engine_timers
nice -n 10 $R $L/09-et-suite timeout 900 python3 -B ptdriver.py $F/figram-engine suite ../../examples
for d in docs/t_need.bot.json docs/t_timers.bot.json ../../examples/pizza.bot.json ../../examples/features.bot.json; do
  b=$(basename $d .bot.json)
  nice -n 10 $R $L/10-et-walk-$b timeout 600 python3 -B ptdriver.py $F/figram-engine walk $d --users 20 --steps 40
done
nice -n 10 $R $L/11-et-host-contract python3 -B check_host_contract.py ../../HOST.md ../../FORMAT.md $F/figram-engine ../../examples
cd $W
nice -n 10 $R $L/12-ffi-build_audit timeout 1200 bash ffi/audit/build_audit.sh
nice -n 10 $R $L/13-ffi-regress timeout 1500 bash ffi/audit/regress.sh
mkdir -p $L/formats
TMPDIR=/tmp/claude-0/-home-user-figram-fsm-library/57129652-0dc1-5f90-93b6-ee8cd24dbb93/scratchpad FIGRAM=$F/figram FIGRAM_ENGINE=$F/figram-engine bash tests/formats/run_all.sh $L/formats 100 > $L/14-formats.txt 2>&1
echo FINAL-GATE-DONE

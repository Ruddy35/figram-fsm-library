#!/usr/bin/env bash
# Дополнительные наборы на fix/memory после P-22 (тулчейн из исходников).
set -u
source /home/user/figram-audit/env.sh
export LEAN_CC=/home/user/figram-audit/fixes/memory/cc-quiet.sh
W=/home/user/figram-audit-worktrees/fix-memory; L=/home/user/figram-audit/fixes/memory/LOGS/round4; R=/home/user/figram-audit/run.sh
cd "$W" || exit 1
$R $L/50-build_audit bash ffi/audit/build_audit.sh
$R $L/51-sanitizers timeout 1700 bash ffi/audit/sanitizers.sh
$R $L/52-mutants timeout 1700 bash ffi/audit/mutants.sh
$R $L/53-stack-stress python3 -B /home/user/figram-audit/fixes/memory/stack_stress.py $W/.lake/build/bin/figram $W/.lake/build/bin/figram-engine $W/audit/core-compiler
(cd audit/core-compiler && $R $L/54-perf_scale python3 -B perf_scale.py --engine $W/.lake/build/bin/figram-engine --figram $W/.lake/build/bin/figram --label p22 --check --only states1000,states3000,unique1000,unique2000,depth64,resume100,global300,global_big,random1000,flat_4000,hist_100x20,hist_200x20,deep_100_x_500leaves_hist --steps 300 --load-timeout 400 --max-load-s 5 --max-step-us 1000 --out $L/54-perf_scale.jsonl)
$R $L/55-formats bash tests/formats/run_all.sh $L/formats
$R $L/56-core-compiler sh audit/core-compiler/run_all.sh $W/.lake/build/bin/figram-engine $W/.lake/build/bin/figram $L/cc
echo SUITES-DONE

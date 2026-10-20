#!/usr/bin/env bash
# fix/final: production-like hosts on the final engine (Python: process figram-engine of fix/final;
# Go: libfigram.a of fix/final). Outputs and logs go to fixes/final (audit evidence untouched).
set -u
source /home/user/figram-audit/env.sh
W=/home/user/figram-audit-worktrees/fix-final
F=/home/user/figram-audit/fixes/final
R=/home/user/figram-audit/run.sh
OUTP=$F/hosts/out/python; OUTG=$F/hosts/out/go
LP=$F/LOGS/hosts/python; LG=$F/LOGS/hosts/go
mkdir -p $OUTP $OUTG $LP $LG
export HOST_OUT=$OUTP HOST_LOGS=$LP FIGRAM_ENGINE=$F/bin-final/figram-engine.stripped HOST_WORK=/tmp/claude-0/-home-user-figram-fsm-library/57129652-0dc1-5f90-93b6-ee8cd24dbb93/scratchpad/hpwork
export HOSTGO_LOGS=$LG HOSTGO_OUT=$OUTG HOSTGO_SCRATCH=/tmp/claude-0/-home-user-figram-fsm-library/57129652-0dc1-5f90-93b6-ee8cd24dbb93/scratchpad/hgwork
cd $W/audit/host-python
case "${1:-all}" in
  py-ok|all) $R $LP/00-suite-ok python3 -I suite.py ok ;;&
  py-red|all) $R $LP/01-suite-red python3 -I suite.py red; $R $LP/02-suite-matrix python3 -I suite.py matrix ;;&
  go-ok|all) cd $W/audit/host-go && $R $LG/00-build bash scripts/build.sh && $R $LG/01-run_all bash scripts/run_all.sh $OUTG ;;&
  go-red|all) cd $W/audit/host-go && $R $LG/02-red_matrix python3 -I scripts/red_matrix.py $OUTG/red_matrix.json /tmp/claude-0/-home-user-figram-fsm-library/57129652-0dc1-5f90-93b6-ee8cd24dbb93/scratchpad/hgwork ;;&
  compare|all) cd $W/audit/host-go && $R $LG/03-compare python3 -I compare/compare.py $OUTG $OUTP /home/user/figram-audit/hosts/shared/corpus --json $OUTG/compare_go_vs_python.json ;;&
esac
echo HOSTS-DONE

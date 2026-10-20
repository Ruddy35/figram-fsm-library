#!/usr/bin/env bash
# fix/final: verify-faults.sh — every injected fault must turn the gate red (one fault per run).
set -u
source /home/user/figram-audit/env.sh
W=/home/user/figram-audit-worktrees/fix-final
F=/home/user/figram-audit/fixes/final
R=/home/user/figram-audit/run.sh
S=/tmp/claude-0/-home-user-figram-fsm-library/57129652-0dc1-5f90-93b6-ee8cd24dbb93/scratchpad
cd $W
mode=${1:-full}
if [ $mode = full ]; then
  for f in none golden-truncated sums-tampered bin-swapped build-warning custom-axiom native-decide theorem-weakened go-missing race-test-not-run coverage-drop coverage-drop-regen engine-bug-golden-edited; do
    GATE_TREE=$W EXPECT_BASE=3 FIGRAM_SHA256SUMS=$F/gate-sums/SUMS FAULT_LOGS=$F/LOGS/faults/full/$f TMPDIR=$S \
      nice -n 10 $R $F/LOGS/faults/full-$f timeout 1700 ./verify-faults.sh full $f
  done
else
  # prebuilt: test-only copy of this tree whose bin/ holds the stripped binaries of this build
  # (bin/ in git is the old official build; the orchestrator rebuilds it in CI)
  P=$S/prebuilt-tree
  for f in none golden-truncated selftest-shrunk fixtures-shrunk random-docs-shrunk json-fuzz-shrunk mutants-shrunk bin-swapped; do
    GATE_TREE=$P EXPECT_BASE=0 FAULT_LOGS=$F/LOGS/faults/prebuilt/$f TMPDIR=$S \
      nice -n 10 $R $F/LOGS/faults/prebuilt-$f timeout 1700 ./verify-faults.sh prebuilt $f
  done
  nice -n 10 $R $F/LOGS/faults/prebuilt-tree-verify-prebuilt bash -c "cd $P && timeout 1700 ./verify-prebuilt.sh"
fi
echo FAULTS-DONE

#!/usr/bin/env bash
set -u
source /home/user/figram-audit/env.sh
export LEAN_CC=/home/user/figram-audit/fixes/memory/cc-quiet.sh
cd /home/user/figram-audit-worktrees/cleanup-int
L=/home/user/figram-audit/cleanup/LOGS/v030-final-gate2
R=/home/user/figram-audit-worktrees/cleanup-int/tests/lib/run_logged.sh
$R $L/01-lake-build lake build
$R $L/02-ffi-build bash ffi/build.sh
$R $L/03-statements lake env lean tests/proofs/Statements.lean
$R $L/04-verify ./verify.sh
$R $L/05-formats bash tests/formats/run_all.sh
$R $L/06-compiler bash tests/compiler/run_all.sh
$R $L/07-sanitizers bash tests/capi/sanitizers.sh
$R $L/08-mutants bash tests/capi/mutants.sh
$R $L/09-proof-controls bash tests/proofs/controls.sh
(cd clients/go && $R $L/10-go-test go test -count=1 ./...)
(cd clients/go && $R $L/11-go-race go test -race -count=1 ./...)
$R $L/12-check_docs python3 ci/check_docs.py
mkdir -p /tmp/claude-0/-home-user/57129652-0dc1-5f90-93b6-ee8cd24dbb93/scratchpad/faultlogs030b
$R $L/13-faults-prebuilt env FAULT_LOGS=/tmp/claude-0/-home-user/57129652-0dc1-5f90-93b6-ee8cd24dbb93/scratchpad/faultlogs030b ./verify-faults.sh prebuilt
for f in $L/*.log; do printf '%s %s\n' "$(basename $f)" "$(grep '^exit_code' $f)"; done > $L/SUMMARY.txt
echo GATE-DONE

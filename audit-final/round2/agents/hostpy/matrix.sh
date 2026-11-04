#!/usr/bin/env bash
# The recorded scenario matrix (each run: LOGS/<name>.log, runs/<name>/summary.json).
cd "$(dirname "$0")"
S=./scen.sh
# --- green: the host as the documents describe it (fence reconciliation, scheduler in the db)
$S g1-shop    --bot shop    --users 150 --duration 60 --crash-p 0.003 --lose 10 --restarts 10
$S g2-payouts --bot payouts --users 150 --duration 60 --crash-p 0.003 --lose 10 --restarts 10
$S g3-booking --bot booking --users 150 --duration 60 --crash-p 0.003 --lose 10 --restarts 10
$S g4-restore --bot shop    --users 120 --duration 50 --restore 15,30 --crash-p 0.002 --lose 5 --restarts 5
$S g5-restore-booking --bot booking --users 120 --duration 50 --restore 15,30 --crash-p 0.002
$S g6-sched-out-triple-rearm --bot shop --users 120 --duration 50 --restore 15,30 --sched-outside --flag rearm --crash-p 0.002
$S g7-sched-out-pair-rearm-purge --bot shop --users 120 --duration 50 --restore 15,30 --sched-outside --flag sched-pair --flag rearm --flag purge-other-runs --crash-p 0.002
# --- negative controls: a broken host must go red
$S n1-no-user-lock   --bot shop    --users 60 --duration 15 --flag no-user-lock
$S n2-random-keys    --bot shop    --users 60 --duration 15 --flag random-keys
$S n3-outcome-after-mark --bot shop --users 60 --duration 15 --flag outcome-after-mark --crash-p 0.05 --crash-points x.between_mark_and_outcome
$S n4-no-reconcile   --bot shop    --users 60 --duration 15 --flag no-reconcile
$S n5-bg-in-barrier  --bot shop    --users 60 --duration 15 --flag bg-in-barrier --quiesce-timeout 30
$S n6-no-barrier     --bot payouts --users 60 --duration 15 --flag no-barrier
$S n7-no-runid-check --bot shop    --users 100 --duration 40 --restore 10,20 --sched-outside --flag rearm --flag no-runid-check
$S n8-keys-seq       --bot shop    --users 100 --duration 40 --restore 10,20 --flag keys-seq
$S n9-store-only-if-seq --bot shop --users 60 --duration 15 --flag store-only-if-seq
BADENGINE_EVERY=30 $S n10-bad-engine --bot shop --users 60 --duration 15 --engine "$PWD/badengine.py"
$S n11-no-dedupe     --bot shop    --users 60 --duration 15 --flag no-dedupe
# --- document traps: a host that follows a reading of the documents
$S t1-read-reconcile --bot shop --users 80 --duration 30 --delayed-pct 6 --flag reconcile-read
$S t1-fence-reconcile --bot shop --users 80 --duration 30 --delayed-pct 6
$S t2-sched-out-triple-norearm --bot shop --users 120 --duration 50 --restore 15,30 --sched-outside --crash-p 0.002
$S t2-sched-out-pair-rearm --bot shop --users 150 --duration 60 --restore 15,30 --sched-outside --flag sched-pair --flag rearm
$S t3-outcome-inline --bot shop --users 30 --duration 10 --flag outcome-inline --quiesce-timeout 60
$S t5-gc-outbox --bot shop --users 60 --duration 15 --flag gc-outbox --quiesce-timeout 30
echo MATRIX DONE

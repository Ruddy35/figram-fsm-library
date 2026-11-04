#!/usr/bin/env bash
cd "$(dirname "$0")"
S=./scen.sh
$S n4-no-reconcile   --bot shop    --users 60 --duration 15 --flag no-reconcile
$S n5-bg-in-barrier  --bot shop    --users 60 --duration 15 --flag bg-in-barrier --quiesce-timeout 30
$S n6-no-barrier     --bot payouts --users 60 --duration 15 --flag no-barrier
$S n7-no-runid-check --bot shop    --users 100 --duration 40 --restore 10,20 --sched-outside --flag rearm --flag no-runid-check
$S n8-keys-seq       --bot shop    --users 100 --duration 40 --restore 10,20 --flag keys-seq
$S n9-store-only-if-seq --bot shop --users 60 --duration 15 --flag store-only-if-seq
BADENGINE_EVERY=30 $S n10-bad-engine --bot shop --users 60 --duration 15 --engine "$PWD/badengine.py"
$S n11-no-dedupe     --bot shop    --users 60 --duration 15 --flag no-dedupe
# --- document traps: a host that follows a reading of the documents
$S t2-sched-out-triple-norearm --bot shop --users 120 --duration 50 --restore 15,30 --sched-outside --crash-p 0.002
$S t3-outcome-inline --bot shop --users 30 --duration 10 --flag outcome-inline --quiesce-timeout 60
echo MATRIX DONE

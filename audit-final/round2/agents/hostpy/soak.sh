#!/usr/bin/env bash
# Soak runs (bounded: load 420 s each, the whole run under 15 min), one at a time.
cd "$(dirname "$0")"
S=./scen.sh
$S soak2-shop    --bot shop    --users 100 --duration 400 --cycle --crash-p 0.0003 --lose 20 --restarts 20 --quiesce-timeout 180
$S soak2-payouts --bot payouts --users 100 --duration 400 --cycle --crash-p 0.0003 --lose 20 --restarts 20 --quiesce-timeout 180
$S soak2-booking --bot booking --users 100 --duration 400 --cycle --crash-p 0.0003 --lose 20 --restarts 20 --quiesce-timeout 180
echo SOAK DONE

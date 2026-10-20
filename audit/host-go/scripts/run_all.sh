#!/usr/bin/env bash
# Runs all 15 shared corpora on the CORRECT Go host and writes HOSTS_SPEC outputs.
#   scripts/run_all.sh [OUTDIR]   (default /home/user/figram-audit/hosts/out/go)
set -u
HERE=$(cd "$(dirname "$0")/.." && pwd)
OUT=${1:-/home/user/figram-audit/hosts/out/go}
LOGS=${HOSTGO_LOGS:-/home/user/figram-audit/agents/host-go/LOGS}/ok   # HOSTGO_LOGS: do not overwrite the audit evidence on a re-run
SCR=${HOSTGO_SCRATCH:-/tmp/hostgo}
export HOSTGO_WORK=$SCR/work
REF=$SCR/ref
mkdir -p "$OUT" "$LOGS" "$HOSTGO_WORK" "$REF"
CORPUS=/home/user/figram-audit/hosts/shared/corpus
rc_all=0
for c in order-1 order-2 booking-1 booking-2 support-1 reminders-1 reminders-2 migration-1 failures-1 failures-2 \
         adversarial-1 load_u1-7 load_u10-7 load_u100-7 load_u1000-7; do
  nice -n 10 timeout 900 /home/user/figram-audit/run.sh "$LOGS/$c" "$HERE/bin/fgrun" run "$CORPUS/$c.jsonl" \
      -out "$OUT/$c.json" -ref "$REF" -quiet
  rc=$?; [ $rc -ne 0 ] && rc_all=1
done
exit $rc_all

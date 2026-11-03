#!/usr/bin/env bash
# plan.sh [NAME...]: run the standard suites on every built defect (or the named ones).
R=/home/user/figram-audit/agents5/redteam
cd $R
STD="conf-golden conf-traces edges snap host random directed diff-small"
GATE="gate-conf gate-traces gate-golden gate-random"
names=${*:-$(ls bin/mut)}
for n in $names; do
  [ -d LOGS/$n ] && [ -f LOGS/$n/diff-small.log ] && continue
  [ -f DEFECTS/$n.full.patch ] && [ -x bin/mut/$n/figram-engine ] || { echo "skip $n (not built yet)"; continue; }
  case $n in
    d14*|d15*|d27*|d28*) S="$STD $GATE check-diff lint-warnings d-engine";;
    d02*|d03*|d04*|d06-*|d12*|d18*|d19*|d20*|d11*|d13c*|d38*) S="$STD $GATE";;
    d09*|d10*|d21*|d22*|d23*|d24*|d31*|d33*) S="$STD d-engine client";;
    *) S="$STD";;
  esac
  echo "=== $n: $S"
  scripts/suites.sh $n $S
done

#!/usr/bin/env bash
cd /home/user/figram-audit/agents5/redteam
for n in d02a-any-token-of-action d02b-any-token d03-latest-as-every; do
  echo "=== $n (gate rerun)"; scripts/suites.sh $n gate-conf gate-traces gate-golden gate-random
done
scripts/plan.sh

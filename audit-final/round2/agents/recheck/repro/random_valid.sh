#!/usr/bin/env bash
# figram random SEED N for many seeds and sizes: every document must be produced (the CLI refuses
# an invalid one with exit 1) and pass `figram check`; count messages with two timers.
F=$1; S=$2; n=0; bad=0; two=0
for seed in $(seq 1 $S); do for m in 2 5 12 30; do
  n=$((n+1))
  if ! "$F" random $seed $m > /tmp/rv.$$ 2>/tmp/rv.$$.err; then bad=$((bad+1)); echo "FAIL random $seed $m: $(head -c 300 /tmp/rv.$$.err)"; continue; fi
  "$F" check /tmp/rv.$$ > /dev/null 2>&1 || { bad=$((bad+1)); echo "CHECK FAIL $seed $m"; }
  two=$((two + $(python3 -I -c "
import json,sys
d=json.load(open(sys.argv[1]))
c=0
def w(o):
    global c
    if 'children' in o:
        [w(v) for v in o['children'].values()]
    elif len(o.get('timers',{}))>=2: c+=1
w(d['bot']); print(c)" /tmp/rv.$$)))
done; done
rm -f /tmp/rv.$$ /tmp/rv.$$.err
echo "documents $n, invalid $bad, messages with two timers $two"
[ $bad = 0 ] && [ $two -gt 0 ]

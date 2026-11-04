#!/usr/bin/env bash
# scen.sh NAME ARGS... — one recorded run: LOGS/NAME.log, runs/NAME/summary.json
cd "$(dirname "$0")"
name=$1; shift
/home/user/figram-audit/run.sh LOGS/$name nice -n 10 timeout 900 python3 run.py --out runs/$name "$@"
python3 -c "
import json,sys; d=json.load(open('runs/$name/summary.json')); print('$name', 'ok=',d['ok'], {k:v['count'] for k,v in d['violations'].items()}, 'crashes',d['host_crashes_injected'], 'tput',d['throughput_steps_per_s_load'], 'outcomes', d['notes'].get('outcomes'), 'stuck', d['notes'].get('stuck_classes'), {k:v for k,v in d['metrics'].items() if k.startswith(('drop','sched','repair','deadlock'))})"

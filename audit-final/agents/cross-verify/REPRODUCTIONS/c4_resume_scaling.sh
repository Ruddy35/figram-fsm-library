#!/usr/bin/env bash
# C4 supporting measurement: step time of one request vs number of `resume` entries K,
# and JSON parse alone (`figram json`) for the same line. Usage: c4_resume_scaling.sh FIGRAM ENGINE SCRATCH K...
F=$1; E=$2; S=$3; shift 3; mkdir -p "$S"
D=$(cd "$(dirname "$0")/.." && pwd)
for K in "$@"; do
  python3 -c "
import json,sys
K=int(sys.argv[1])
snap={'at':'cart','resume':{f's{i}':'catalog' for i in range(K)}}
print(json.dumps({'id':1,'op':'step','snapshot':snap,'event':'btn:checkout','guards':{'cart_not_empty':True}}))" "$K" > "$S/req-$K.json"
  s=$(date +%s%N); nice -n 10 timeout 300 "$E" "$D/examples/pizza.bot.json" < "$S/req-$K.json" > "$S/out-$K.json"; rc=$?; e=$(date +%s%N)
  s2=$(date +%s%N); nice -n 10 timeout 300 "$F" json < "$S/req-$K.json" > /dev/null; rc2=$?; e2=$(date +%s%N)
  echo "K=$K bytes=$(wc -c < "$S/req-$K.json") engine_step_ms=$(( (e-s)/1000000 )) rc=$rc parse_only_ms=$(( (e2-s2)/1000000 )) rc=$rc2 reply=$(head -c 60 "$S/out-$K.json")"
done

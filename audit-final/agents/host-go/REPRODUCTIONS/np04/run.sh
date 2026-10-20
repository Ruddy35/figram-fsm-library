#!/usr/bin/env bash
# Builds init_race.c against the fresh libfigram.a and runs it RUNS times (fresh process each).
set -u
D=$(cd "$(dirname "$0")" && pwd); F=/home/user/figram-audit/fresh/ffi-out
gcc -O1 -o "$D/init_race" "$D/init_race.c" -L$F -l:libfigram.a -L$F/lean -Wl,--start-group -lInit -lleanrt \
    -Wl,--end-group -lstdc++ -luv -lgmp -lm -ldl -lpthread -lrt || exit 2
RUNS=${1:-300}; MODE=${2:-concurrent}; ok=0; fail=0; declare -A rcs
for i in $(seq "$RUNS"); do
  timeout 20 "$D/init_race" /home/user/figram-audit/hosts/shared/bots/pizza.bot.json 8 "$MODE" > /dev/null 2>"$D/last.err"; rc=$?
  rcs[$rc]=$(( ${rcs[$rc]:-0} + 1 ))
done
echo "mode=$MODE runs=$RUNS"; for k in "${!rcs[@]}"; do echo "rc=$k: ${rcs[$k]} runs"; done

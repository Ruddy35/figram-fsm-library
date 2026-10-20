#!/usr/bin/env bash
# Отрицательный контроль для perf_soak.sh: тот же soak.py на «текущем» прокси-движке —
# наклон RSS обязан быть большим положительным.
set -eu
W=/home/user/figram-audit-worktrees/host-perf
export FIGRAM_ENGINE=${1:-$W/bin/figram-engine}
exec nice -n 10 timeout 200 python3 $W/audit/host-perf/soak.py $W/audit/host-perf/leaky_engine.sh $W/examples/pizza.bot.json --seconds 60 --sample 5 --users 1000

#!/usr/bin/env bash
# Длительный прогон одного процесса движка на pizza: perf_soak.sh [SECONDS=600] [ENGINE]
set -eu
SEC=${1:-600}; E=${2:-/home/user/figram-audit-worktrees/host-perf/bin/figram-engine}
exec nice -n 10 timeout $((SEC + 120)) python3 /home/user/figram-audit-worktrees/host-perf/audit/host-perf/soak.py "$E" /home/user/figram-audit-worktrees/host-perf/examples/pizza.bot.json --seconds "$SEC" --sample 10 --users 1000

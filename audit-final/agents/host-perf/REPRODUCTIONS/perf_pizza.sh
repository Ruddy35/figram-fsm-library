#!/usr/bin/env bash
# pizza: задержка шага через канал (wall и CPU движка из schedstat) и пропускная способность без Python.
set -eu
E=${1:-/home/user/figram-audit-worktrees/host-perf/bin/figram-engine}
T=$(mktemp -d)
nice -n 10 python3 /home/user/figram-audit-worktrees/host-perf/audit/host-perf/bench_engine.py "$E" /home/user/figram-audit-worktrees/host-perf/examples/pizza.bot.json --steps 50000 --reqs "$T/reqs.jsonl"
nice -n 10 /home/user/figram-audit-worktrees/host-perf/audit/host-perf/replay.sh "$E" /home/user/figram-audit-worktrees/host-perf/examples/pizza.bot.json "$T/reqs.jsonl" "$T/out.jsonl"

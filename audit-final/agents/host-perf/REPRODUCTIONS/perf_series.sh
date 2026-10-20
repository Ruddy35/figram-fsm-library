#!/usr/bin/env bash
# Серия масштабируемости: perf_series.sh SERIES [ENGINE]  (states|unique|global|branches|depth|resume|random|readme|global_big)
set -eu
S=${1:-states}; E=${2:-/home/user/figram-audit-worktrees/host-perf/bin/figram-engine}
T=$(mktemp -d)
nice -n 10 timeout 870 python3 /home/user/figram-audit-worktrees/host-perf/audit/host-perf/run_series.py --engine "$E" --figram /home/user/figram-audit-worktrees/host-perf/bin/figram --series "$S" --steps 5000 --load-timeout 300 --out "$T/results.jsonl" --workdir "$T" >/dev/null
python3 /home/user/figram-audit-worktrees/host-perf/audit/host-perf/summarize.py "$T/results.jsonl"

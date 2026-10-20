#!/usr/bin/env bash
# Регрессия HP-01/HP-07 на исправленном host_sim.py (ветка audit/host-perf, c8bb903):
#   без барьера (буквальный старый HOST §1.3) — ожидается код 1 (красный),
#   с барьером (новый §1.3) — ожидается код 0.
set -u
E=${1:-/home/user/figram-audit-worktrees/host-perf/bin/figram-engine}
cd /home/user/figram-audit-worktrees/host-perf/ffi/python-example
nice -n 10 timeout 300 python3 host_sim.py "$E" ../../examples/pizza.bot.json --no-barrier; a=$?
nice -n 10 timeout 300 python3 host_sim.py "$E" ../../examples/pizza.bot.json; b=$?
echo "без барьера: код $a (ожидается 1); с барьером: код $b (ожидается 0)"
[ $a -eq 1 ] && [ $b -eq 0 ]

#!/usr/bin/env bash
# host_sim.py против всех мутаций движка на pizza; выжившие = тест хоста их не видит.
#   host_sim_mutants.sh [ENGINE] [base|fixed]
#   base  — host_sim.py базового коммита d69bbaf (условия всегда True)
#   fixed — host_sim.py ветки audit/host-perf (условия по данным, барьер)
set -eu
E=${1:-/home/user/figram-audit-worktrees/host-perf/bin/figram-engine}
V=${2:-base}
W=/home/user/figram-audit-worktrees/host-perf
ARGS=()
if [ "$V" = base ]; then
  D=$(mktemp -d)
  for f in host_sim.py figram_client.py protocol.py; do git -C $W show d69bbaf:ffi/python-example/$f > "$D/$f"; done
  ARGS=(--host-sim "$D")
fi
exec nice -n 10 timeout 900 python3 $W/audit/host-perf/host_sim_mutants.py "$E" $W/examples/pizza.bot.json "${ARGS[@]}"

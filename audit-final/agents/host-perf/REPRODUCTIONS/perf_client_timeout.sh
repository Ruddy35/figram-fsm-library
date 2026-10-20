#!/usr/bin/env bash
# Клиент Python (таймаут 10 с по умолчанию) на документе из 3000 сообщений: загрузка дольше таймаута.
set -eu
E=${1:-/home/user/figram-audit-worktrees/host-perf/bin/figram-engine}
T=$(mktemp -d)
python3 /home/user/figram-audit-worktrees/host-perf/audit/host-perf/gen_doc.py --states 3000 --branches 2 --resume 1 --timers 0 > "$T/big.bot.json"
exec nice -n 10 timeout 600 python3 /home/user/figram-audit-worktrees/host-perf/audit/host-perf/client_load_timeout.py "$E" "$T/big.bot.json" 3

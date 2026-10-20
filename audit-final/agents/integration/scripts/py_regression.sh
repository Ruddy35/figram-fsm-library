#!/bin/sh
# py_regression.sh WORKTREE LABEL ENGINE: python client suites (tests of the worktree, client of the worktree)
W=$1; LABEL=$2; E=$3
L=/home/user/figram-audit/agents/integration/LOGS; R=/home/user/figram-audit/run.sh
cd "$W/ffi/python-example" || exit 2
sha256sum figram_client.py > "$L/py-$LABEL-client.sha256"
one() { n=$1; shift; $R "$L/py-$LABEL-$n" nice -n 10 timeout 900 "$@" >/dev/null; rc=$?
        printf '%-20s rc=%s  %s\n' "$n" "$rc" "$(tail -1 "$L/py-$LABEL-$n.stdout" | cut -c1-160)"; }
one client_robustness python3 client_robustness.py "$E" ../../examples/pizza.bot.json ../../examples/features.bot.json
one client_faults     python3 client_faults.py "$E" ../../examples/pizza.bot.json
one client_contract   python3 client_contract.py "$E" ../../examples/features.bot.json

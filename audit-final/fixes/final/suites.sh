#!/usr/bin/env bash
# fix/final: audit suites on the final tree (every command logged with run.sh).
#   suites.sh [cc|et|formats|ffi|go|client|docs|all]
set -u
source /home/user/figram-audit/env.sh
W=/home/user/figram-audit-worktrees/fix-final
B=/home/user/figram-audit/fixes/final/bin-final
L=/home/user/figram-audit/fixes/final/LOGS/suites
R=/home/user/figram-audit/run.sh
S=/tmp/claude-0/-home-user-figram-fsm-library/57129652-0dc1-5f90-93b6-ee8cd24dbb93/scratchpad
E=$W/.lake/build/bin/figram-engine
F=$W/.lake/build/bin/figram
mkdir -p $L
cd $W
case "${1:-all}" in
  docs|all)
    nice -n 10 $R $L/01-check_docs python3 -I audit/docs/check_docs.py
    nice -n 10 $R $L/02-g_protocol_docs python3 -I tests/formats/g_protocol_docs.py
    nice -n 10 $R $L/03-ffi-doc_check bash ffi/audit/doc_check.sh
    nice -n 10 $R $L/04-test_strict_gate_clean timeout 1500 bash audit/proofs/test_strict_gate_clean.sh
    nice -n 10 $R $L/05-strict_gate timeout 1500 bash audit/proofs/strict_gate.sh
    git status --porcelain --untracked-files=no > $L/05-strict_gate-git-status.txt
    ;;&
  cc|all)
    mkdir -p $L/cc
    (cd audit/core-compiler && nice -n 10 $R $L/10-cc-run_all timeout 1750 env FIGRAM_ALLOW_MUTANTS=1 sh ./run_all.sh $E $F $L/cc)
    (cd audit/core-compiler && nice -n 10 $R $L/11-cc-lint_warnings python3 -B lint_warnings.py --figram $F)
    (cd audit/core-compiler && nice -n 10 $R $L/12-cc-check_messages-fc07 python3 -B check_messages_diff.py --old /home/user/figram-audit/fixes/core/base-bin/figram --new $F --docs 20 --fc07)
    (cd audit/core-compiler && nice -n 10 $R $L/13-cc-compile_same python3 -B compile_same.py --old-figram /home/user/figram-audit/fixes/core/base-bin/figram --old-engine /home/user/figram-audit/fixes/core/base-bin/figram-engine --new-figram $F --new-engine $E --docs 60)
    ;;&
  et|all)
    nice -n 10 $R $L/20-et-suite timeout 1700 python3 -B audit/engine_timers/ptdriver.py $E suite examples
    for d in audit/engine_timers/docs/t_need.bot.json audit/engine_timers/docs/t_timers.bot.json examples/pizza.bot.json examples/features.bot.json; do
      b=$(basename $d .bot.json)
      nice -n 10 $R $L/21-et-walk-$b timeout 600 python3 -B audit/engine_timers/ptdriver.py $E walk $d --users 20 --steps 40
    done
    for d in features pizza; do
      nice -n 10 $R $L/22-et-mutants-$d timeout 1700 python3 -B audit/engine_timers/ptdriver.py $E mutants examples/$d.bot.json
    done
    nice -n 10 $R $L/23-check_host_contract python3 -B audit/engine_timers/check_host_contract.py HOST.md FORMAT.md $E examples
    ;;&
  formats|all)
    mkdir -p $L/formats
    TMPDIR=$S FIGRAM=$F FIGRAM_ENGINE=$E nice -n 10 $R $L/30-formats-run_all timeout 1750 bash tests/formats/run_all.sh $L/formats 250
    ;;&
  ffi|all)
    nice -n 10 $R $L/40-ffi-build_audit timeout 1200 bash ffi/audit/build_audit.sh
    nice -n 10 $R $L/41-ffi-regress timeout 1500 bash ffi/audit/regress.sh
    nice -n 10 $R $L/42-ffi-sanitizers timeout 1700 bash ffi/audit/sanitizers.sh
    nice -n 10 $R $L/43-ffi-mutants timeout 1700 bash ffi/audit/mutants.sh
    nice -n 10 $R $L/44-ffi-b01_build timeout 1200 bash ffi/audit/b01_build.sh
    ;;&
  go|all)
    (cd ffi/go-example && nice -n 10 $R $L/50-go-example-test-race timeout 1700 env CC=gcc GOMAXPROCS=4 go test -race -count=1 ./...)
    (cd ffi/go-example && nice -n 10 $R $L/51-go-example-test timeout 1700 env CC=gcc go test -count=1 ./...)
    (cd audit/host-go && nice -n 10 $R $L/52-host-go-test-race timeout 1700 bash scripts/build.sh test -race -count=1 ./...)
    ;;&
  client|all)
    P=ffi/python-example
    nice -n 10 $R $L/60-client_robustness timeout 1500 python3 $P/client_robustness.py $E examples/pizza.bot.json examples/features.bot.json
    nice -n 10 $R $L/61-client_faults timeout 900 python3 $P/client_faults.py $E examples/pizza.bot.json
    nice -n 10 $R $L/62-client_contract timeout 900 python3 $P/client_contract.py $E examples/features.bot.json
    nice -n 10 $R $L/63-protocol_edges timeout 900 python3 $P/protocol_edges.py $E examples/features.bot.json
    $F trace examples/pizza.bot.json 200 40 7 > $S/pizza-trace.jsonl 2>/dev/null
    nice -n 10 $R $L/64-loadtest timeout 900 python3 $P/loadtest.py $E examples/pizza.bot.json $S/pizza-trace.jsonl 64 2
    nice -n 10 $R $L/65-host_sim timeout 900 python3 $P/host_sim.py $E examples/pizza.bot.json
    nice -n 10 $R $L/66-host_sim-no-barrier timeout 900 python3 $P/host_sim.py $E examples/pizza.bot.json --no-barrier
    ;;&
esac
echo SUITES-DONE

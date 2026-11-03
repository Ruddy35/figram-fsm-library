#!/usr/bin/env bash
# suites.sh NAME SUITE...: run test suites against bin/mut/NAME (or the original binaries for NAME=base).
# The engine is bin/mut/NAME/figram-engine if present, else the original; the same for figram
# (only Format defects build a mutated figram; the reference traces in ref/ come from the original).
set -u
NAME=$1; shift
R=/home/user/figram-audit/agents5/redteam
W=/home/user/figram-audit-worktrees/a5-redteam
RUN=/home/user/figram-audit/run.sh
E=$R/bin/figram-engine; F=$R/bin/figram
[ -x "$R/bin/mut/$NAME/figram-engine" ] && E=$R/bin/mut/$NAME/figram-engine
[ -x "$R/bin/mut/$NAME/figram" ] && F=$R/bin/mut/$NAME/figram
L=$R/LOGS/$NAME; mkdir -p "$L"
D="--oracle-opt timer_accept=owned --oracle-opt exit_cancels=snapshot"
P=$W/tests/engine; C=$W/tests/compiler
cd "$W"
N="nice -n 10 timeout 900"
summ() { # summ SUITE REGEX
  local s=$1 rx=$2 rc
  rc=$(grep -m1 '^exit_code:' "$L/$s.log" | awk '{print $2}')
  printf '%-22s exit=%-3s %s\n' "$s" "$rc" "$(grep -E -m3 "$rx" "$L/$s.stdout" | tr '\n' ' ' | cut -c1-230)"
}
for s in "$@"; do
  case $s in
    conf) $RUN "$L/conf" $N python3 $P/conformance.py $E --golden examples/golden.jsonl \
            examples/pizza.bot.json=$R/ref/pizza.jsonl examples/features.bot.json=$R/ref/features.jsonl >/dev/null
          summ conf '^requests' ;;
    conf-golden) $RUN "$L/conf-golden" $N python3 $P/conformance.py $E --golden examples/golden.jsonl >/dev/null
          summ conf-golden '^requests' ;;
    conf-traces) $RUN "$L/conf-traces" $N python3 $P/conformance.py $E \
            examples/pizza.bot.json=$R/ref/pizza.jsonl examples/features.bot.json=$R/ref/features.jsonl >/dev/null
          summ conf-traces '^requests' ;;
    random) $RUN "$L/random" $N python3 $P/random_docs.py $F $E 100 12 >/dev/null; summ random '^documents' ;;
    mutation) $RUN "$L/mutation" $N env FIGRAM_ALLOW_MUTANTS=1 python3 $P/mutation.py $E --golden examples/golden.jsonl \
            examples/pizza.bot.json=$R/ref/pizza.jsonl examples/features.bot.json=$R/ref/features.jsonl >/dev/null
          summ mutation '^detected|survived' ;;
    host) $RUN "$L/host" $N python3 $P/host_sim.py $E examples/pizza.bot.json >/dev/null
          python3 $R/scripts/host_expect.py "$L/host.stdout" > "$L/host.expect" 2>&1
          printf '%-22s exit=%-3s %s | gate-expect: %s\n' host "$(grep -m1 '^exit_code:' "$L/host.log" | awk '{print $2}')" \
            "$(grep -E -m2 'FAIL|outcomes:' "$L/host.stdout" | tr '\n' ' ' | cut -c1-200)" "$(tr '\n' ' ' < "$L/host.expect" | cut -c1-200)" ;;
    snap) $RUN "$L/snap" $N python3 $P/snapshot_fixtures.py $E examples/features.bot.json schemas/snapshot.schema.json >/dev/null
          summ snap '^snapshots|^seq boundary' ;;
    edges) $RUN "$L/edges" $N python3 $P/protocol_edges.py $E examples/features.bot.json >/dev/null
          summ edges '^protocol edge|^FAIL' ;;
    client) $RUN "$L/client_contract" $N python3 $P/client_contract.py $E examples/features.bot.json >/dev/null; summ client_contract 'FAIL|ok'
            $RUN "$L/client_faults" $N python3 $P/client_faults.py $E examples/pizza.bot.json >/dev/null; summ client_faults 'FAIL'
            $RUN "$L/client_robust" $N python3 $P/client_robustness.py $E examples/pizza.bot.json examples/features.bot.json >/dev/null; summ client_robust 'FAIL' ;;
    selftest) $RUN "$L/selftest" $N python3 $P/harness_selftest.py $E examples/pizza.bot.json $R/ref/pizza.jsonl $R/ref/features.jsonl >/dev/null
          printf '%-22s exit=%-3s ok=%s FAIL=%s\n' selftest "$(grep -m1 '^exit_code:' "$L/selftest.log" | awk '{print $2}')" "$(grep -c '^ok ' "$L/selftest.stdout")" "$(grep -c '^FAIL' "$L/selftest.stdout")" ;;
    load) $RUN "$L/load" $N python3 $P/loadtest.py $E examples/pizza.bot.json $R/ref/pizza.jsonl 8 1 >/dev/null; summ load '^threads' ;;
    directed) (cd $C && $RUN "$L/directed" $N python3 directed.py --engine $E --group conformance >/dev/null); summ directed '^cases run|^FAIL' ;;
    directed-findings) (cd $C && $RUN "$L/directed-findings" $N python3 directed.py --engine $E --group findings $D --oracle-opt need=min >/dev/null); summ directed-findings '^cases run' ;;
    diff-small) (cd $C && $RUN "$L/diff-small" $N python3 diff_random.py --engine $E --docs 100 --steps 60 --seed 1 $D >/dev/null); summ diff-small '"stats"|DIVERGENCE' ;;
    diff-gate) (cd $C && $RUN "$L/diff-gate" nice -n 10 timeout 840 python3 diff_random.py --engine $E --docs 600 --steps 120 --seed 1 $D >/dev/null); summ diff-gate '"stats"|DIVERGENCE' ;;
    metamorphic) (cd $C && $RUN "$L/metamorphic" $N python3 metamorphic.py --engine $E --docs 40 --steps 60 >/dev/null); summ metamorphic 'FAIL|cases|total|violation' ;;
    mutants-red) (cd $C && $RUN "$L/mutants-red" $N python3 mutants_red.py --engine $E --docs 8 --steps 100 $D >/dev/null); summ mutants-red 'survived|killed|stats' ;;
    check-diff) (cd $C && $RUN "$L/check-diff" $N python3 check_diff.py --engine $E --docs 20 >/dev/null); summ check-diff 'disagree|stats|cases|FAIL' ;;
    lint-gaps) (cd $C && $RUN "$L/lint-gaps" $N python3 lint_gaps.py --figram $F --engine $E >/dev/null); summ lint-gaps 'FAIL|ok|cases' ;;
    lint-warnings) (cd $C && $RUN "$L/lint-warnings" $N python3 lint_warnings.py --figram $F >/dev/null); summ lint-warnings 'FAIL|ok|cases' ;;
    formats) $RUN "$L/formats" env FIGRAM=$F FIGRAM_ENGINE=$E bash tests/formats/run_all.sh "$L/formats-logs" 50 >/dev/null; summ formats 'UNEXPECTED|unexpected exit codes' ;;
    d-engine) $RUN "$L/d-engine" env FIGRAM=$F FIGRAM_ENGINE=$E $N python3 tests/formats/d_engine_requests.py >/dev/null; summ d-engine 'FAIL|cases|mismatch|ok' ;;
    golden-cmp) mkdir -p "$L/golden"; $RUN "$L/golden-cmp" bash -c "$F golden $L/golden/out && for f in golden.jsonl pizza.bot.json features.bot.json; do cmp $L/golden/out/\$f examples/\$f || exit 1; done; echo same" >/dev/null; summ golden-cmp 'same|differ|scenarios' ;;
    gate-conf) G=$R/bin/mut/$NAME/figram-gate; [ -x "$G" ] || G=$R/bin/mut/$NAME/figram; [ -x "$G" ] || { echo "gate-conf: no figram-gate"; continue; }
          for d in pizza features; do $G trace examples/$d.bot.json 200 40 7 --directed > "$L/gate-$d.jsonl" 2> "$L/gate-$d.cov"; done
          $RUN "$L/gate-conf" $N python3 $P/conformance.py $E --golden examples/golden.jsonl \
            examples/pizza.bot.json="$L/gate-pizza.jsonl" examples/features.bot.json="$L/gate-features.jsonl" >/dev/null
          printf '%-22s exit=%-3s %s | cov: %s\n' gate-conf "$(grep -m1 '^exit_code:' "$L/gate-conf.log" | awk '{print $2}')" "$(grep -m1 '^requests' "$L/gate-conf.stdout")" "$(grep -h covered "$L"/gate-*.cov | tr '\n' ' ')" ;;
    gate-traces) :
          $RUN "$L/gate-traces" $N python3 $P/conformance.py $E \
            examples/pizza.bot.json="$L/gate-pizza.jsonl" examples/features.bot.json="$L/gate-features.jsonl" >/dev/null; summ gate-traces '^requests' ;;
    gate-golden) G=$R/bin/mut/$NAME/figram-gate; [ -x "$G" ] || G=$R/bin/mut/$NAME/figram; mkdir -p "$L/ggolden"
          $RUN "$L/gate-golden" bash -c "$G golden $L/ggolden/out && for f in golden.jsonl pizza.bot.json features.bot.json; do cmp $L/ggolden/out/\$f examples/\$f || exit 1; done; echo same" >/dev/null; summ gate-golden 'same|differ|scenarios' ;;
    gate-random) G=$R/bin/mut/$NAME/figram-gate; [ -x "$G" ] || G=$R/bin/mut/$NAME/figram; $RUN "$L/gate-random" $N python3 $P/random_docs.py $G $E 100 12 >/dev/null; summ gate-random '^documents' ;;
    *) echo "unknown suite $s"; exit 2 ;;
  esac
done

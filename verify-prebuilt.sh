#!/usr/bin/env bash
# Проверка без Lean: собранные бинарники из bin/ (Linux x86_64, зависят от libc).
# Покрывает движок, утилиту, клиент, обвязку, контракт хоста, текстовый JSON.
# НЕ покрывает: сборку, теоремы, C-прослойку и Go — для них нужен ./verify.sh с Lean 4.35.0.
# Соответствие bin/ исходникам доказывает только ./verify.sh (сборка воспроизводима).
#
#   ./verify-prebuilt.sh          полная проверка (~90 с на одном ядре)
#   ./verify-prebuilt.sh --quick  сокращённая (~30 с): меньше документов и фаззинга,
#                                 без мутаций и самопроверки обвязки — НЕ замена полной
set -euo pipefail
cd "$(dirname "$0")"
QUICK=0; [ "${1:-}" = "--quick" ] && QUICK=1
F=bin/figram
E=bin/figram-engine
P=ffi/python-example
(cd bin && sha256sum -c SHA256SUMS)
python3 -c "import jsonschema" 2>/dev/null || { echo "нужен Python-пакет jsonschema: pip install jsonschema"; exit 1; }
T=$(mktemp -d)
trap 'rm -rf "$T"' EXIT
LOGS="$(pwd)/verify-logs"
rm -rf "$LOGS"; mkdir -p "$LOGS"
# logged ИМЯ КОМАНДА…: полный вывод шага — в verify-logs/ИМЯ.log; при успехе
# печатается итог (последние строки), при отказе — все строки «НЕТ» и хвост журнала.
# clean ИМЯ КОМАНДА…: как logged, но stderr отдельно и обязан быть пустым.
clean() {
  local name=$1; shift
  if ! "$@" > "$LOGS/$name.log" 2> "$LOGS/$name.err"; then
    echo "ОТКАЗ шага «$name», журнал: verify-logs/$name.log"
    grep -E "^НЕТ" "$LOGS/$name.log" | head -40 || true
    tail -25 "$LOGS/$name.log"; tail -10 "$LOGS/$name.err"; exit 1
  fi
  if [ -s "$LOGS/$name.err" ]; then
    echo "НЕТ: шаг «$name» печатает в stderr:"; head -10 "$LOGS/$name.err"; exit 1
  fi
  tail -1 "$LOGS/$name.log"
}
logged() {
  local name=$1; shift
  if "$@" > "$LOGS/$name.log" 2>&1; then
    tail -${SHOW:-1} "$LOGS/$name.log"
  else
    local rc=$?
    echo "ОТКАЗ шага «$name» (код $rc), журнал: verify-logs/$name.log"
    grep -E "^НЕТ|РАСХОЖДЕНИЕ|Traceback|ОТКАЗ|выжила" "$LOGS/$name.log" | head -40 || true
    echo "--- хвост журнала:"; tail -25 "$LOGS/$name.log"
    exit $rc
  fi
}
LAST=$SECONDS
step() { echo "   ($(( SECONDS - LAST )) с)"; LAST=$SECONDS; echo; echo "== $*"; }
quiet_stderr() { if [ -s "$1" ]; then echo "НЕТ: исключения в stderr:"; head -5 "$1"; exit 1; fi; }

step "Фиксированные сценарии совпадают с сохранёнными"
$F golden "$T/golden" >/dev/null
cmp "$T/golden/golden.jsonl" examples/golden.jsonl && echo "ok"

step "Соответствие движка"
$F trace examples/pizza.bot.json 200 40 7 > "$T/pizza.jsonl"
$F trace examples/features.bot.json 200 40 7 > "$T/features.jsonl"
python3 $P/conformance.py $E --golden examples/golden.jsonl \
  examples/pizza.bot.json="$T/pizza.jsonl" examples/features.bot.json="$T/features.jsonl"
logged random_docs python3 $P/random_docs.py $F $E $([ $QUICK = 1 ] && echo 20 || echo 100) 12

if [ $QUICK = 0 ]; then
  step "Мутационное тестирование"
  SHOW=3 logged mutation python3 $P/mutation.py $E --golden examples/golden.jsonl \
    examples/pizza.bot.json="$T/pizza.jsonl" examples/features.bot.json="$T/features.jsonl"
fi

step "Снимок и текстовый JSON"
SHOW=2 logged snapshots python3 $P/snapshot_fixtures.py $E examples/features.bot.json snapshot.schema.json
logged json_fuzz python3 $P/json_fuzz.py $F $([ $QUICK = 1 ] && echo 5000 || echo 30000) 1

step "Клиент Python"
clean client_faults python3 $P/client_faults.py $E examples/pizza.bot.json
clean client_contract python3 $P/client_contract.py $E examples/features.bot.json

if [ $QUICK = 0 ]; then
  step "Самопроверка обвязки"
  logged selftest python3 $P/harness_selftest.py $E examples/pizza.bot.json "$T/pizza.jsonl" "$T/features.jsonl"
fi

step "Контракт хоста и нагрузка"
logged host python3 $P/host_sim.py $E examples/pizza.bot.json
logged load python3 $P/loadtest.py $E examples/pizza.bot.json "$T/pizza.jsonl" 64 2
echo "   ($(( SECONDS - LAST )) с)"

echo
if [ $QUICK = 1 ]; then
  echo "СОКРАЩЁННАЯ ПРОВЕРКА ПРОЙДЕНА: пропущены мутации и самопроверка обвязки, 20 документов вместо 100,"
  echo "5000 случаев фаззинга вместо 30000. Это не замена полной проверки."
else
  echo "ПРОВЕРКА БЕЗ LEAN ПРОЙДЕНА (сборка, теоремы, C и Go не проверялись — см. ./verify.sh)"
fi

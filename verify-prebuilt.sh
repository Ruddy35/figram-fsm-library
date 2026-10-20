#!/usr/bin/env bash
# Проверка без Lean: собранные бинарники из bin/ (Linux x86_64, зависят от libc).
# Покрывает движок, утилиту, клиент, обвязку, контракт хоста, текстовый JSON.
# НЕ покрывает: сборку, теоремы, C-прослойку и Go — для них нужен ./verify.sh с Lean 4.35.0.
# Соответствие bin/ исходникам доказывает только ./verify.sh (сборка воспроизводима).
#
#   ./verify-prebuilt.sh          полная проверка (~90 с на одном ядре)
#   ./verify-prebuilt.sh --quick  сокращённая (~30 с): меньше документов и фаззинга,
#                                 без мутаций и самопроверки обвязки — НЕ замена полной
#
# Код выхода: 0 — полная проверка прошла; 1 — провал; 3 — сокращённая (--quick) прошла:
# это не полная проверка, CI обязан считать код 3 провалом.
set -euo pipefail
shopt -s inherit_errexit
cd "$(dirname "$0")"
fail() { echo "НЕТ: $*"; exit 1; }
case "${1:-}" in
  "") QUICK=0;;
  --quick) QUICK=1;;
  *) fail "неизвестный аргумент «$1» (допустим только --quick)";;
esac
MIN_MUTANTS=107        # мутаций в двух документах примеров (уменьшение — провал)
MIN_SELFTEST_OK=37     # пройденных проверок самопроверки обвязки
F=bin/figram
E=bin/figram-engine
P=ffi/python-example
(cd bin && sha256sum -c SHA256SUMS)
python3 -c "import jsonschema" 2>/dev/null || fail "нужен Python-пакет jsonschema: pip install jsonschema"
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
    exit 1
  fi
}
LAST=$SECONDS
step() { echo "   ($(( SECONDS - LAST )) с)"; LAST=$SECONDS; echo; echo "== $*"; }
# expect ФАЙЛ РЕГВЫР УСЛОВИЕ ОПИСАНИЕ: в файле есть строка по регулярному выражению,
# и условие (выражение Python над числами групп g[0], g[1], …) истинно. Иначе — провал.
expect() {
  python3 - "$@" <<'PY' || exit 1
import re, sys
path, rx, cond, what = sys.argv[1:5]
text = open(path, encoding="utf-8", errors="replace").read()
m = re.search(rx, text, re.M)
if not m:
    print(f"НЕТ: {what}: в выводе нет строки вида «{rx}»"); sys.exit(1)
g = [int(re.sub(r"[^0-9-]", "", x)) for x in m.groups()]
if not eval(cond, {}, {"g": g}):
    print(f"НЕТ: {what}: «{m.group(0)}» не удовлетворяет {cond}"); sys.exit(1)
PY
}

step "Фиксированные сценарии совпадают с сохранёнными"
$F golden "$T/golden" > "$T/golden.out"
expect "$T/golden.out" '^сценариев: (\d+)$' "g[0] > 0 and g[0] == $(grep -c . examples/golden.jsonl)" "фиксированные сценарии"
for f in golden.jsonl pizza.bot.json features.bot.json; do
  cmp "$T/golden/$f" "examples/$f" || fail "examples/$f не совпадает с выводом figram golden"
done
echo "ok ($(cat "$T/golden.out"))"

step "Соответствие движка"
for d in pizza features; do
  $F trace examples/$d.bot.json 200 40 7 > "$T/$d.jsonl" 2> "$T/$d.cov"
  cat "$T/$d.cov"
  expect "$T/$d.cov" '^покрыто вариантов стрелок: (\d+) из (\d+)$' 'g[1] > 0 and g[0] == g[1]' "покрытие $d"
done
python3 $P/conformance.py $E --golden examples/golden.jsonl \
  examples/pizza.bot.json="$T/pizza.jsonl" examples/features.bot.json="$T/features.jsonl" | tee "$T/conf.out"
expect "$T/conf.out" '^запросов (\d+), расхождений (\d+)$' 'g[0] > 0 and g[1] == 0' "соответствие"
NDOCS=$([ $QUICK = 1 ] && echo 20 || echo 100)
logged random_docs python3 $P/random_docs.py $F $E $NDOCS 12
expect "$LOGS/random_docs.log" '^документов (\d+), запросов (\d+), документов с расхождениями (\d+)$' \
  "g[0] == $NDOCS and g[1] > 0 and g[2] == 0" "случайные документы"

if [ $QUICK = 0 ]; then
  step "Мутационное тестирование"
  # --mutants/--mutate — тестовый режим движка, только при FIGRAM_ALLOW_MUTANTS=1 (mutation.py ставит и сам).
  SHOW=3 logged mutation env FIGRAM_ALLOW_MUTANTS=1 python3 $P/mutation.py $E --golden examples/golden.jsonl \
    examples/pizza.bot.json="$T/pizza.jsonl" examples/features.bot.json="$T/features.jsonl"
  expect "$LOGS/mutation.log" '^обнаружено (\d+) из (\d+) мутаций' "g[1] >= $MIN_MUTANTS and g[0] == g[1]" "мутации"
fi

step "Снимок и текстовый JSON"
SHOW=2 logged snapshots python3 $P/snapshot_fixtures.py $E examples/features.bot.json snapshot.schema.json
expect "$LOGS/snapshots.log" '^снимков (\d+), ответов (\d+), расхождений схемы и движка (\d+)$' \
  'g[0] >= 24 and g[0] == g[1] and g[2] == 0' "снимки"
expect "$LOGS/snapshots.log" '^граница seq: ok$' 'True' "граница seq"
logged protocol_edges python3 $P/protocol_edges.py $E examples/features.bot.json
expect "$LOGS/protocol_edges.log" '^граничных случаев протокола (\d+), ответов (\d+), расхождений (\d+)$' \
  'g[0] >= 106 and g[0] == g[1] and g[2] == 0' "граничные случаи протокола"
NFUZZ=$([ $QUICK = 1 ] && echo 5000 || echo 30000)
logged json_fuzz python3 $P/json_fuzz.py $F $NFUZZ 1
expect "$LOGS/json_fuzz.log" '^случаев (\d+): принято обоими (\d+), отвергнуто обоими (\d+), расхождений (\d+), записей не читается обратно (\d+) из (\d+)$' \
  "g[0] == $NFUZZ and g[1] > 0 and g[2] > 0 and g[1] + g[2] == g[0] and g[3] == 0 and g[4] == 0 and g[5] == g[1]" "фаззинг JSON"

step "Клиент Python"
clean client_faults python3 $P/client_faults.py $E examples/pizza.bot.json
clean client_contract python3 $P/client_contract.py $E examples/features.bot.json
clean client_robustness python3 $P/client_robustness.py $E examples/pizza.bot.json examples/features.bot.json

if [ $QUICK = 0 ]; then
  step "Самопроверка обвязки"
  logged selftest python3 $P/harness_selftest.py $E examples/pizza.bot.json "$T/pizza.jsonl" "$T/features.jsonl"
  n_ok=$(grep -c '^ok ' "$LOGS/selftest.log" || true)
  n_bad=$(grep -c '^НЕТ' "$LOGS/selftest.log" || true)
  [ "$n_ok" -ge "$MIN_SELFTEST_OK" ] && [ "$n_bad" -eq 0 ] \
    || fail "самопроверка обвязки: пройдено $n_ok (нужно не меньше $MIN_SELFTEST_OK), провалено $n_bad"
fi

step "Контракт хоста и нагрузка"
logged host python3 $P/host_sim.py $E examples/pizza.bot.json
expect "$LOGS/host.log" '^  пользователей (\d+): итог как ожидалось \(.*\) у (\d+), заказов в CRM (\d+), обработано событий (\d+) из (\d+) .*потерянных обновлений (\d+), невыполненных действий (\d+)$' \
  'g[0] > 0 and g[0] == g[1] and 0 < g[2] <= g[0] and g[3] == g[4] and g[5] == 0 and g[6] == 0' "контракт хоста"
expect "$LOGS/host.log" '^  тест ловит нарушение: да(?: \(попыток контроля: \d+\))?$' 'True' "контрольный хост без контракта"
logged load python3 $P/loadtest.py $E examples/pizza.bot.json "$T/pizza.jsonl" 64 2
expect "$LOGS/load.log" '^потоков +(\d+), процессов (\d+): шагов (\d+) .*расхождений (\d+),' \
  'g[0] == 64 and g[2] > 0 and g[3] == 0' "нагрузка"
echo "   ($(( SECONDS - LAST )) с)"

echo
if [ $QUICK = 1 ]; then
  echo "СОКРАЩЁННАЯ ПРОВЕРКА ПРОЙДЕНА: пропущены мутации и самопроверка обвязки, 20 документов вместо 100,"
  echo "5000 случаев фаззинга вместо 30000. Это не замена полной проверки (код выхода 3)."
  exit 3
else
  echo "ПРОВЕРКА БЕЗ LEAN ПРОЙДЕНА (сборка, теоремы, C и Go не проверялись — см. ./verify.sh)"
fi

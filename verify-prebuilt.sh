#!/usr/bin/env bash
# Checks prebuilt binaries without Lean (Linux x86_64, they depend on libc).
# Покрывает движок, утилиту, клиент, обвязку, контракт хоста, текстовый JSON.
# НЕ покрывает: сборку, теоремы, C-прослойку и Go — для них нужен ./verify.sh с Lean 4.35.0.
# That the binaries were built from these sources is shown only by ./verify.sh with
# FIGRAM_SHA256SUMS (the build is reproducible).
#
#   ./verify-prebuilt.sh DIR          полная проверка (~90 с на одном ядре)
#   ./verify-prebuilt.sh --quick DIR  сокращённая (~30 с): меньше документов и фаззинга,
#                                     без мутаций и самопроверки обвязки — НЕ замена полной
# DIR (or FIGRAM_BIN=DIR) is the directory with `figram` and `figram-engine`, e.g. an unpacked
# release or .lake/build/bin. If DIR/SHA256SUMS exists, `sha256sum -c` must accept it.
#
# Код выхода: 0 — полная проверка прошла; 1 — провал; 3 — сокращённая (--quick) прошла:
# это не полная проверка, CI обязан считать код 3 провалом. 2 — usage error.
set -euo pipefail
shopt -s inherit_errexit
fail() { echo "НЕТ: $*"; exit 1; }
usage() {
  echo "usage: $0 [--quick] DIR   (DIR contains figram and figram-engine; or set FIGRAM_BIN=DIR)" >&2
  [ $# = 0 ] || echo "$0: $*" >&2
  exit 2
}
QUICK=0; BIN=""
for a in "$@"; do
  case $a in
    --quick) QUICK=1;;
    -*) usage "unknown option $a";;
    *) [ -z "$BIN" ] || usage "more than one DIR"; BIN=$a;;
  esac
done
BIN=${BIN:-${FIGRAM_BIN:-}}
[ -n "$BIN" ] || usage
for b in figram figram-engine; do [ -x "$BIN/$b" ] || usage "$BIN/$b is not an executable file"; done
BIN=$(cd "$BIN" && pwd)
cd "$(dirname "$0")"
MIN_MUTANTS=107        # мутаций в двух документах примеров (уменьшение — провал)
MIN_SELFTEST_OK=37     # пройденных проверок самопроверки обвязки
F=$BIN/figram
E=$BIN/figram-engine
P=tests/engine
echo "binaries: $BIN"
if [ -f "$BIN/SHA256SUMS" ]; then
  (cd "$BIN" && sha256sum -c SHA256SUMS)
else
  echo "no $BIN/SHA256SUMS: checksum check skipped"
fi
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
expect "$T/golden.out" '^scenarios: (\d+)$' "g[0] > 0 and g[0] == $(grep -c . examples/golden.jsonl)" "фиксированные сценарии"
for f in golden.jsonl pizza.bot.json features.bot.json; do
  cmp "$T/golden/$f" "examples/$f" || fail "examples/$f не совпадает с выводом figram golden"
done
echo "ok ($(cat "$T/golden.out"))"

step "Соответствие движка"
for d in pizza features; do
  $F trace examples/$d.bot.json 200 40 7 > "$T/$d.jsonl" 2> "$T/$d.cov"
  cat "$T/$d.cov"
  expect "$T/$d.cov" '^arrow variants covered: (\d+) of (\d+)$' 'g[1] > 0 and g[0] == g[1]' "покрытие $d"
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

step "Память, потоки и переполнение стека движка (P-09, P-22)"
logged engine_memory python3 $P/engine_memory.py $E examples/pizza.bot.json
expect "$LOGS/engine_memory.log" '^память движка: резерв адресов (\d+) МБ, занято (\d+) МБ, потоков (\d+)$' \
  'g[0] <= 64 and g[1] <= 16 and g[2] == 1' "память и потоки движка"
expect "$LOGS/engine_memory.log" '^ok  ulimit -v 32 МБ: код 0, ответов 2$' 'True' "движок под ulimit -v 32 МБ"

step "Снимок и текстовый JSON"
SHOW=2 logged snapshots python3 $P/snapshot_fixtures.py $E examples/features.bot.json schemas/snapshot.schema.json
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

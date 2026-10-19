#!/usr/bin/env bash
# Полная проверка проекта. Код выхода 0 — всё в порядке.
set -euo pipefail
cd "$(dirname "$0")"
F=.lake/build/bin/figram
E=.lake/build/bin/figram-engine
P=ffi/python-example
T=$(mktemp -d)
python3 -c "import jsonschema" 2>/dev/null || { echo "нужен Python-пакет jsonschema: pip install jsonschema"; exit 1; }
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
step() { echo; echo "== $*"; }

step "Сборка без предупреждений"
lake build 2>&1 | tee "$T/build.log" | tail -1
if grep -q "warning" "$T/build.log"; then grep -A3 warning "$T/build.log"; exit 1; fi

step "Приложенные бинарники bin/ собраны ровно из этих исходников"
for b in figram figram-engine; do cp .lake/build/bin/$b "$T/$b" && strip "$T/$b"; done
# (сборка ffi/out нужна шагам C и Go ниже)
(cd "$T" && sha256sum figram figram-engine) | diff - bin/SHA256SUMS && echo "ok: хеши совпадают побайтно"

step "Теоремы без sorry"
cat > "$T/ax.lean" <<'LEAN'
import Figram
open Figram Figram.Format
#print axioms Chart.compile_correct
#print axioms Chart.session_correct
#print axioms Chart.chart_run_valid
#print axioms Machine.step_state_valid
#print axioms Machine.step_guard_local
#print axioms decode_encode
#print axioms encode_injective
#print axioms decode_requires
#print axioms toChart_strip
#print axioms check_roundtrip
#print axioms Engine.decSnapshot_encSnapshot
#print axioms Engine.step_snapOK
#print axioms Engine.handle_step
#print axioms Engine.reply_readable
LEAN
lake env lean "$T/ax.lean" | tee "$T/ax.log"
if grep -q sorry "$T/ax.log"; then exit 1; fi

step "Фиксированные сценарии совпадают с сохранёнными"
$F golden "$T/golden" >/dev/null
cmp "$T/golden/golden.jsonl" examples/golden.jsonl && echo "ok"

step "Соответствие движка: фиксированные и случайные сценарии"
$F trace examples/pizza.bot.json 200 40 7 > "$T/pizza.jsonl"
$F trace examples/features.bot.json 200 40 7 > "$T/features.jsonl"
python3 $P/conformance.py $E --golden examples/golden.jsonl \
  examples/pizza.bot.json="$T/pizza.jsonl" examples/features.bot.json="$T/features.jsonl"

step "Соответствие на 100 случайных документах"
logged random_docs python3 $P/random_docs.py $F $E 100 12

step "Мутационное тестирование"
SHOW=3 logged mutation python3 $P/mutation.py $E --golden examples/golden.jsonl \
  examples/pizza.bot.json="$T/pizza.jsonl" examples/features.bot.json="$T/features.jsonl"

step "Контракт хоста"
logged host python3 $P/host_sim.py $E examples/pizza.bot.json

step "CLI: ошибки аргументов дают ненулевой код"
for args in "trace examples/pizza.bot.json abc 1 7" "trace examples/pizza.bot.json -1 1 7" \
            "trace examples/pizza.bot.json 1 0 7" "trace examples/pizza.bot.json 1 1 7 extra"; do
  if $F $args >/dev/null 2>&1; then echo "ожидалась ошибка: $args"; exit 1; fi
done
echo "ok"

step "Огромный показатель степени: быстрый контролируемый отказ"
printf '{"figram":1,"bot":{"initial":"a","children":{"a":{"timers":{"t":1e1000000000}}}}}' > "$T/big.json"
start=$(date +%s)
set +e
timeout 5 $F check "$T/big.json" >/dev/null 2>"$T/big.err"
rc=$?
set -e
# Ожидается именно код 1 с диагностикой; 124 — таймаут, 13x — аварийное завершение.
if [ "$rc" -ne 1 ] || ! grep -q "показатель степени" "$T/big.err" || [ $(( $(date +%s) - start )) -ge 5 ]; then
  echo "НЕТ: код $rc, stderr: $(head -1 "$T/big.err")"; exit 1
fi
echo "ok (код 1, $(head -1 "$T/big.err"))"

step "Снимок: схема и движок согласованы, граница seq"
SHOW=2 logged snapshots python3 $P/snapshot_fixtures.py $E examples/features.bot.json snapshot.schema.json

step "Текстовый слой JSON: дифференциально против Python json"
logged json_fuzz python3 $P/json_fuzz.py $F 30000 1
cat > "$T/lax.py" <<'PY'
#!/usr/bin/env python3
# Контроль чувствительности: «парсер» без ограничений формата.
import json, sys
for line in sys.stdin.buffer:
    try:
        print("OK " + json.dumps(json.loads(line.decode().rstrip("\n"))), flush=True)
    except Exception:
        print("ERR x", flush=True)
PY
chmod +x "$T/lax.py"
if python3 $P/json_fuzz.py "$T/lax.py" 3000 1 >/dev/null 2>&1; then
  echo "НЕТ: фаззер не заметил парсер без ограничений"; exit 1
else echo "контроль: парсер без ограничений обнаружен"; fi

step "C-прослойка: невалидный UTF-8, огромные входы, 16 потоков, ASan и UBSan"
./ffi/build.sh >/dev/null
PFX=$(lean --print-prefix)
O=.lake/build/ir/Figram
OBJS=""; for m in Core Chart Compile Lint Format FormatIO Engine Api; do OBJS="$OBJS $O/$m.c.o.export"; done
LIBS="-Lffi/out/lean -Wl,--start-group -lInit -lleanrt -Wl,--end-group -Wl,-Bstatic -lc++ -lc++abi -luv -lgmp -Wl,-Bdynamic -lm -ldl -lpthread -lrt"
gcc -c -g -fsanitize=address,undefined -I"$PFX/include" -o "$T/ffi.o" ffi/figram_ffi.c 2>/dev/null
gcc -c -g -fsanitize=address,undefined -o "$T/io.o" ffi/c_harness_io.c 2>/dev/null
gcc -g -fsanitize=address,undefined -o "$T/harness" ffi/c_harness.c "$T/io.o" "$T/ffi.o" $OBJS $LIBS 2>/dev/null
SHOW=2 logged c_asan env ASAN_OPTIONS=detect_leaks=0 UBSAN_OPTIONS=halt_on_error=1 "$T/harness" examples/pizza.bot.json
# Память при загрузке и выгрузке — на обычной сборке (у ASan карантин памяти).
gcc -O1 -o "$T/harness_plain" ffi/c_harness.c ffi/c_harness_io.c -Lffi/out -l:libfigram.a $LIBS 2>/dev/null
logged c_reload "$T/harness_plain" examples/pizza.bot.json memory

step "Клиент Python: сбои движка, контракт методов, чистый stderr"
clean client_faults python3 $P/client_faults.py $E examples/pizza.bot.json
clean client_contract python3 $P/client_contract.py $E examples/features.bot.json

step "Тестовая обвязка ловит испорченный, неполный, пустой и зависший движок"
$F trace examples/pizza.bot.json 20 30 7 > "$T/small.jsonl" 2>/dev/null
logged selftest python3 $P/harness_selftest.py $E examples/pizza.bot.json "$T/pizza.jsonl" "$T/features.jsonl"

step "Нагрузочный тест: 64 потока, 2 процесса"
logged load python3 $P/loadtest.py $E examples/pizza.bot.json "$T/pizza.jsonl" 64 2

if command -v go >/dev/null; then
  step "Go: встроенный движок, сценарии из многих горутин"
  (cd ffi/go-example && CC=${CC:-gcc} go build -o gofig . \
     && ./gofig -rounds 3 -bots 4 ../../examples/pizza.bot.json "$T/pizza.jsonl" \
     && CC=${CC:-gcc} go build -race -o gofig-race . \
     && GOMAXPROCS=$(( $(nproc) > 4 ? $(nproc) : 4 )) ./gofig-race -rounds 3 -bots 3 ../../examples/pizza.bot.json "$T/small.jsonl")
  for a in "-rounds 0" "-bots 0"; do
    if ffi/go-example/gofig $a examples/pizza.bot.json "$T/small.jsonl" >/dev/null 2>&1; then
      echo "НЕТ: Go-проверка приняла $a"; exit 1; fi
  done
  echo "-rounds 0 и -bots 0: Go-проверка красная"
  for k in recovered-broken recovered-false no-id extra-error empty seq-bool null-snapshot; do
    if ffi/go-example/gofig -corrupt $k examples/pizza.bot.json "$T/small.jsonl" >/dev/null 2>&1; then
      echo "НЕТ: Go-проверка не заметила порчу $k"; exit 1; fi
  done
  echo "7 видов порчи ответов: Go-проверка красная"
  logged go_reload ffi/go-example/gofig -reload 5000 examples/pizza.bot.json "$T/small.jsonl"
  # Один словарь запроса из многих горутин: Request не должен его изменять.
  (cd ffi/go-example && CC=${CC:-gcc} GOMAXPROCS=4 go test -race -count=1 . > "$LOGS/go_shared_map.log" 2>&1) \
    || { echo "НЕТ: гонка при общем словаре запроса"; grep -m3 -A3 "DATA RACE" "$LOGS/go_shared_map.log"; exit 1; }
  echo "общий словарь запроса из 8 горутин: гонок нет"
  # Повреждённый сценарий рядом с корректным обязан провалить Go-проверку.
  (head -1 "$T/small.jsonl"; echo '{oops') > "$T/corrupt.jsonl"
  if ffi/go-example/gofig examples/pizza.bot.json "$T/corrupt.jsonl" >/dev/null 2>&1; then
    echo "НЕТ: Go-проверка пропустила повреждённый сценарий"; exit 1
  else echo "повреждённый сценарий: Go-проверка красная"; fi
fi

echo; echo "ВСЁ В ПОРЯДКЕ"

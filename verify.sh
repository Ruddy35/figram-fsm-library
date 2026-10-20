#!/usr/bin/env bash
# Полная проверка проекта.
# Код выхода: 0 — выполнены ВСЕ шаги, все прошли, отступлений нет («ВСЁ В ПОРЯДКЕ»);
#             1 — провал: любой шаг, несовпадение хешей или сценариев, чужая аксиома,
#                 изменённая формулировка теоремы, ноль проверок, неполное покрытие;
#             3 — все выполненные шаги прошли, но были ЯВНО разрешённые отступления
#                 (это не «всё в порядке»; CI обязан считать код 3 провалом):
#   FIGRAM_SHA256SUMS=файл  — сверять пересборку с этим файлом, а не с bin/SHA256SUMS
#                             (другой тулчейн); bin/ при этом не сверяются с исходниками;
#   FIGRAM_WAIVE=go         — разрешить отсутствие go (Go-часть не выполняется).
set -euo pipefail
shopt -s inherit_errexit
cd "$(dirname "$0")"
F=.lake/build/bin/figram
E=.lake/build/bin/figram-engine
P=ffi/python-example
# Нижние границы числа проверок (значения базовой версии; уменьшение — провал).
MIN_MUTANTS=107        # мутаций в двух документах примеров
MIN_SELFTEST_OK=37     # пройденных проверок самопроверки обвязки
WAIVED=()
fail() { echo "НЕТ: $*"; exit 1; }
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
# ccstep ИМЯ КОМАНДА…: сборка C; диагностика компилятора сохраняется и печатается при отказе.
ccstep() {
  local name=$1; shift
  "$@" 2> "$LOGS/$name.cc.log" || { cat "$LOGS/$name.cc.log"; fail "не собирается $name"; }
}
step() { echo; echo "== $*"; }

step "Происхождение прогона"
echo "дата: $(date -u +%FT%TZ); ядер: $(nproc)"
if git rev-parse HEAD >/dev/null 2>&1; then
  dirty=$(git status --porcelain --untracked-files=no | wc -l)
  echo "коммит: $(git rev-parse HEAD); изменённых отслеживаемых файлов: $dirty"
else
  echo "коммит: нет git-репозитория"
fi
echo "verify.sh sha256: $(sha256sum verify.sh | cut -d' ' -f1)"
echo "lean: $(lean --version)"
echo "python: $(python3 --version 2>&1); jsonschema $(python3 -c 'import importlib.metadata as m; print(m.version("jsonschema"))')"
if command -v go >/dev/null; then echo "go: $(go version)"; else echo "go: не установлен"; fi

step "Сборка без предупреждений"
lake build 2>&1 | tee "$T/build.log" | tail -1
grep -q "^Build completed successfully" "$T/build.log" || fail "lake build не сообщил об успешной сборке"
# Предупреждение Lean/Lake — строка «warning: файл:…» (или «файл:…: warning:») и задача «⚠ [i/n]».
# Не просто слово «warning»: модуль Figram.Warnings печатается как «✔ [i/n] Built Figram.Warnings».
WARN_RX='(^|[[:space:]:])warning:|^⚠'
if grep -qiE "$WARN_RX" "$T/build.log"; then grep -iE -A3 "$WARN_RX" "$T/build.log"; fail "предупреждения сборки"; fi

step "Приложенные бинарники bin/ собраны ровно из этих исходников"
SUMS=${FIGRAM_SHA256SUMS:-bin/SHA256SUMS}
[ "$(awk '{print $2}' "$SUMS" | sort | tr '\n' ' ')" = "figram figram-engine " ] \
  || fail "$SUMS: ожидались ровно две записи, figram и figram-engine"
for b in figram figram-engine; do cp .lake/build/bin/$b "$T/$b"; strip "$T/$b"; done
if ! (cd "$T" && sha256sum figram figram-engine) | diff - "$SUMS"; then
  fail "пересобранные бинарники не совпадают с $SUMS"
fi
# Сами bin/* обязаны совпадать с bin/SHA256SUMS, иначе подменённый bin/ прошёл бы проверку.
(cd bin && sha256sum --quiet -c SHA256SUMS) || fail "bin/* не совпадают с bin/SHA256SUMS"
if [ "$SUMS" = bin/SHA256SUMS ]; then
  echo "ok: хеши совпадают побайтно"
else
  echo "ok: пересборка совпадает с $SUMS (bin/ с исходниками НЕ сверялись)"
  WAIVED+=("хеши сверены с $SUMS, а не с bin/SHA256SUMS: соответствие bin/ исходникам не проверено")
fi

step "Теоремы: только стандартные аксиомы, формулировки закреплены"
# Список теорем, которые README.md и AUDIT.md называют доказанными, — ci/theorems.lean.
lake env lean ci/theorems.lean > "$T/ax.log" 2>&1 || { cat "$T/ax.log"; fail "lean не обработал ci/theorems.lean"; }
grep -v '^STMT ' "$T/ax.log" || true
python3 - "$T/ax.log" ci/theorems.lean <<'PY' || exit 1
import re, sys
lines = open(sys.argv[1], encoding="utf-8").read().splitlines()
src = open(sys.argv[2], encoding="utf-8").read()
listed = re.findall(r"^#print axioms (\S+)$", src, re.M)
stated = re.findall(r"^\s*``(\S+?),?$", src, re.M)
ALLOWED = {"propext", "Classical.choice", "Quot.sound"}
seen, stmts, bad = {}, set(), []
if not listed or sorted(listed) != sorted(stated):
    bad.append(f"списки теорем в ci/theorems.lean расходятся: {sorted(set(listed) ^ set(stated))}")
for line in lines:
    m = re.fullmatch(r"'([^']+)' depends on axioms: \[(.*)\]", line)
    if m:
        seen[m.group(1)] = {a.strip() for a in m.group(2).split(",") if a.strip()}
    elif re.fullmatch(r"'([^']+)' does not depend on any axioms", line):
        seen[line.split("'")[1]] = set()
    elif line.startswith("STMT "):
        stmts.add(line.split()[1])
    elif line.strip():
        bad.append(f"неожиданный вывод lean: {line}")
for t in listed:
    if t not in seen:
        bad.append(f"{t}: нет строки #print axioms")
    elif seen[t] - ALLOWED:
        bad.append(f"{t}: недопустимые аксиомы {sorted(seen[t] - ALLOWED)}")
    if t not in stmts:
        bad.append(f"{t}: нет формулировки (STMT)")
for b in bad:
    print("НЕТ:", b)
if not bad:
    print(f"теорем {len(listed)}: аксиомы только из {sorted(ALLOWED)}")
sys.exit(1 if bad else 0)
PY
grep '^STMT ' "$T/ax.log" > "$T/statements.txt" || true
if ! diff ci/THEOREMS.expected "$T/statements.txt" > "$T/statements.diff"; then
  head -40 "$T/statements.diff"
  fail "формулировки теорем отличаются от ci/THEOREMS.expected (менять формулировку — только осознанно, вместе с этим файлом)"
fi
echo "формулировки $(wc -l < ci/THEOREMS.expected) теорем совпадают с ci/THEOREMS.expected"
# Конструкции, которые обходят ядро или подменяют доказанный код при исполнении.
if grep -nE '^\s*(@\[[^]]*\]\s*)?((private|protected|noncomputable|unsafe)\s+)*axiom\s|native_decide|implemented_by|@\[extern|\bsorry\b|\badmit\b|skipKernelTC|ofReduceBool|ofReduceNat' \
     Figram.lean Main.lean EngineMain.lean Figram/*.lean; then
  fail "в исходниках Lean запрещённые конструкции (axiom, native_decide, implemented_by, extern, sorry, admit, skipKernelTC)"
fi
echo "в исходниках нет axiom, native_decide, implemented_by, extern, sorry, admit"

step "Теоремы: строгий шлюз — все теоремы исходников и окружения, формулировки, leanchecker"
# audit/proofs/strict_gate.sh (proofs): #print axioms для ВСЕХ теорем из исходников (число сверяется),
# AxiomGate по окружению (теоремы, определения, opaque модулей Figram.*), закреплённые формулировки
# README/AUDIT (Statements.lean), запрет run_elab/modifyEnv/addDeclCore/unsafe/csimp и
# leanchecker: ядро заново проверяет каждое объявление.
SHOW=4 logged proofs bash audit/proofs/strict_gate.sh
grep -q '^STRICT GATE: PASS$' "$LOGS/proofs.log" || fail "строгий шлюз доказательств не сообщил PASS"

step "Фиксированные сценарии совпадают с сохранёнными"
$F golden "$T/golden" > "$T/golden.out"
expect "$T/golden.out" '^сценариев: (\d+)$' "g[0] > 0 and g[0] == $(grep -c . examples/golden.jsonl)" "фиксированные сценарии"
for f in golden.jsonl pizza.bot.json features.bot.json; do
  cmp "$T/golden/$f" "examples/$f" || fail "examples/$f не совпадает с выводом figram golden"
done
echo "ok ($(cat "$T/golden.out"))"

step "Соответствие движка: фиксированные и случайные сценарии"
for d in pizza features; do
  $F trace examples/$d.bot.json 200 40 7 > "$T/$d.jsonl" 2> "$T/$d.cov"
  cat "$T/$d.cov"
  expect "$T/$d.cov" '^покрыто вариантов стрелок: (\d+) из (\d+)$' 'g[1] > 0 and g[0] == g[1]' "покрытие $d"
done
python3 $P/conformance.py $E --golden examples/golden.jsonl \
  examples/pizza.bot.json="$T/pizza.jsonl" examples/features.bot.json="$T/features.jsonl" | tee "$T/conf.out"
expect "$T/conf.out" '^запросов (\d+), расхождений (\d+)$' 'g[0] > 0 and g[1] == 0' "соответствие"

step "Соответствие на 100 случайных документах"
logged random_docs python3 $P/random_docs.py $F $E 100 12
expect "$LOGS/random_docs.log" '^документов (\d+), запросов (\d+), документов с расхождениями (\d+)$' \
  'g[0] == 100 and g[1] > 0 and g[2] == 0' "случайные документы"

step "Мутационное тестирование"
# --mutants/--mutate — тестовый режим движка, только при FIGRAM_ALLOW_MUTANTS=1 (mutation.py ставит и сам).
SHOW=3 logged mutation env FIGRAM_ALLOW_MUTANTS=1 python3 $P/mutation.py $E --golden examples/golden.jsonl \
  examples/pizza.bot.json="$T/pizza.jsonl" examples/features.bot.json="$T/features.jsonl"
expect "$LOGS/mutation.log" '^обнаружено (\d+) из (\d+) мутаций' "g[1] >= $MIN_MUTANTS and g[0] == g[1]" "мутации"

step "Контракт хоста"
logged host python3 $P/host_sim.py $E examples/pizza.bot.json
expect "$LOGS/host.log" '^  пользователей (\d+): итог как ожидалось \(.*\) у (\d+), заказов в CRM (\d+), обработано событий (\d+) из (\d+) .*потерянных обновлений (\d+), невыполненных действий (\d+)$' \
  'g[0] > 0 and g[0] == g[1] and 0 < g[2] <= g[0] and g[3] == g[4] and g[5] == 0 and g[6] == 0' "контракт хоста"
expect "$LOGS/host.log" '^  тест ловит нарушение: да(?: \(попыток контроля: \d+\))?$' 'True' "контрольный хост без контракта"

step "CLI: ошибки аргументов дают ненулевой код"
for args in "trace examples/pizza.bot.json abc 1 7" "trace examples/pizza.bot.json -1 1 7" \
            "trace examples/pizza.bot.json 1 0 7" "trace examples/pizza.bot.json 1 1 7 extra"; do
  if $F $args >/dev/null 2>&1; then fail "ожидалась ошибка: $args"; fi
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
  fail "код $rc, stderr: $(head -1 "$T/big.err")"
fi
echo "ok (код 1, $(head -1 "$T/big.err"))"

step "Память движка: запуск под ulimit -v 64 и 256 МБ, резерв адресов ≤ 512 МБ, занято ≤ 64 МБ"
logged engine_memory python3 $P/engine_memory.py $E examples/pizza.bot.json
expect "$LOGS/engine_memory.log" '^память движка: резерв адресов (\d+) МБ, занято (\d+) МБ$' \
  'g[0] <= 512 and g[1] <= 64' "память движка"
expect "$LOGS/engine_memory.log" '^ok  ulimit -v 64 МБ: код 0, ответов 2$' 'True' "движок под ulimit -v 64 МБ"

step "Снимок: схема и движок согласованы, граница seq; restart, условия, жетоны, hello"
SHOW=2 logged snapshots python3 $P/snapshot_fixtures.py $E examples/features.bot.json snapshot.schema.json
expect "$LOGS/snapshots.log" '^снимков (\d+), ответов (\d+), расхождений схемы и движка (\d+)$' \
  'g[0] >= 24 and g[0] == g[1] and g[2] == 0' "снимки"
expect "$LOGS/snapshots.log" '^граница seq: ok$' 'True' "граница seq"
logged protocol_edges python3 $P/protocol_edges.py $E examples/features.bot.json
expect "$LOGS/protocol_edges.log" '^граничных случаев протокола (\d+), ответов (\d+), расхождений (\d+)$' \
  'g[0] >= 106 and g[0] == g[1] and g[2] == 0' "граничные случаи протокола"

step "Текстовый слой JSON: дифференциально против Python json"
logged json_fuzz python3 $P/json_fuzz.py $F 30000 1
expect "$LOGS/json_fuzz.log" '^случаев (\d+): принято обоими (\d+), отвергнуто обоими (\d+), расхождений (\d+), записей не читается обратно (\d+) из (\d+)$' \
  'g[0] == 30000 and g[1] > 0 and g[2] > 0 and g[1] + g[2] == g[0] and g[3] == 0 and g[4] == 0 and g[5] == g[1]' "фаззинг JSON"
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
set +e
python3 $P/json_fuzz.py "$T/lax.py" 3000 1 > "$T/lax.out" 2>&1
rc=$?
set -e
# Контроль засчитывается, только если фаззер завершился именно из-за найденных расхождений.
if [ "$rc" -ne 1 ] || ! grep -q "РАСХОЖДЕНИЕ" "$T/lax.out" || grep -q "Traceback" "$T/lax.out"; then
  tail -5 "$T/lax.out"; fail "контроль чувствительности: фаззер не обнаружил парсер без ограничений (код $rc)"
fi
echo "контроль: парсер без ограничений обнаружен ($(grep -m1 '^случаев' "$T/lax.out"))"

step "C-прослойка: невалидный UTF-8, огромные входы, 16 потоков, ASan и UBSan (инструментирована только прослойка)"
./ffi/build.sh > "$LOGS/ffi_build.log" 2>&1 || { tail -20 "$LOGS/ffi_build.log"; fail "ffi/build.sh"; }
PFX=$(lean --print-prefix)
O=.lake/build/ir/Figram
OBJS=""; for m in Core Chart Compile Lint Format FormatIO Engine Api; do OBJS="$OBJS $O/$m.c.o.export"; done
# Флаги компоновки для этого тулчейна Lean пишет ffi/build.sh (B-01): официальный релиз — libc++ из
# префикса Lean, сборка из исходников — системные статические libuv/libgmp и libstdc++.
LIBS=$(cat ffi/out/LDFLAGS.runtime)
ccstep ffi_asan gcc -c -g -fsanitize=address,undefined -fexceptions -I"$PFX/include" -o "$T/ffi.o" ffi/figram_ffi.c
ccstep io_asan gcc -c -g -fsanitize=address,undefined -o "$T/io.o" ffi/c_harness_io.c
ccstep harness_asan gcc -g -fsanitize=address,undefined -o "$T/harness" ffi/c_harness.c "$T/io.o" "$T/ffi.o" ffi/out/figram_guard.o ffi/out/figram_rt.o $OBJS $LIBS
SHOW=2 logged c_asan env ASAN_OPTIONS=detect_leaks=0 UBSAN_OPTIONS=halt_on_error=1 "$T/harness" examples/pizza.bot.json
expect "$LOGS/c_asan.log" '^16 потоков × (\d+) вызовов к одному автомату: расхождений (\d+)$' 'g[0] > 0 and g[1] == 0' "C, 16 потоков"
# Память при загрузке и выгрузке — на обычной сборке (у ASan карантин памяти).
ccstep harness_plain gcc -O1 -o "$T/harness_plain" ffi/c_harness.c ffi/c_harness_io.c $(cat ffi/out/LDFLAGS)
logged c_reload "$T/harness_plain" examples/pizza.bot.json memory
expect "$LOGS/c_reload.log" '^(\d+) загрузок и выгрузок: рост памяти' 'g[0] > 0' "C, загрузки и выгрузки"

step "C-ABI ffi/figram.h: init без падения хоста, NULL, дескрипторы, NUL, UTF-8, предел, сигналы"
# ffi/audit/regress.sh (P-09, P-18, P-21, P-22, FFI-01/05/06): каждый подтест — отдельный процесс;
# init под RLIMIT_AS (prlimit), 2×300 процессов с одновременной (в т.ч. ленивой) инициализацией.
CA="$T/ffi-audit"; mkdir -p "$CA"
for t in contract stress; do
  ccstep "$t" gcc -g -O1 -pthread -I"$PFX/include" -o "$CA/$t" "ffi/audit/$t.c" $(cat ffi/out/LDFLAGS)
done
ccstep oom gcc -g -O1 -pthread -Wl,--wrap=malloc -o "$CA/oom" ffi/audit/oom.c $(cat ffi/out/LDFLAGS)
cp "$T/harness_plain" "$CA/harness_plain"
logged c_contract bash ffi/audit/regress.sh "$CA"
expect "$LOGS/c_contract.log" '^регрессионных проверок C-ABI (\d+), провалено (\d+)$' 'g[0] >= 31 and g[1] == 0' "C-ABI"
# Заголовок с контрактом самодостаточен для C и C++.
ccstep header_c gcc -std=c99 -Wall -Wextra -Werror -pedantic -fsyntax-only -x c ffi/figram.h
ccstep header_cxx g++ -Wall -Wextra -Werror -fsyntax-only -x c++ ffi/figram.h
echo "ffi/figram.h компилируется как C99 и C++ без предупреждений"

step "Клиент Python: сбои движка, контракт методов, чистый stderr"
clean client_faults python3 $P/client_faults.py $E examples/pizza.bot.json
clean client_contract python3 $P/client_contract.py $E examples/features.bot.json
clean client_robustness python3 $P/client_robustness.py $E examples/pizza.bot.json examples/features.bot.json

step "Тестовая обвязка ловит испорченный, неполный, пустой и зависший движок"
$F trace examples/pizza.bot.json 20 30 7 > "$T/small.jsonl" 2>/dev/null
logged selftest python3 $P/harness_selftest.py $E examples/pizza.bot.json "$T/pizza.jsonl" "$T/features.jsonl"
n_ok=$(grep -c '^ok ' "$LOGS/selftest.log" || true)
n_bad=$(grep -c '^НЕТ' "$LOGS/selftest.log" || true)
[ "$n_ok" -ge "$MIN_SELFTEST_OK" ] && [ "$n_bad" -eq 0 ] \
  || fail "самопроверка обвязки: пройдено $n_ok (нужно не меньше $MIN_SELFTEST_OK), провалено $n_bad"

step "Нагрузочный тест: 64 потока, 2 процесса"
logged load python3 $P/loadtest.py $E examples/pizza.bot.json "$T/pizza.jsonl" 64 2
expect "$LOGS/load.log" '^потоков +(\d+), процессов (\d+): шагов (\d+) .*расхождений (\d+),' \
  'g[0] == 64 and g[2] > 0 and g[3] == 0' "нагрузка"

if command -v go >/dev/null; then
  step "Go: встроенный движок, сценарии из многих горутин"
  G=ffi/go-example
  (cd $G && CC=${CC:-gcc} go build -o gofig .) > "$LOGS/go_build.log" 2>&1 || { cat "$LOGS/go_build.log"; fail "go build"; }
  (cd $G && CC=${CC:-gcc} go build -race -o gofig-race .) > "$LOGS/go_build_race.log" 2>&1 \
    || { cat "$LOGS/go_build_race.log"; fail "go build -race"; }
  SHOW=1 logged go_conf $G/gofig -rounds 3 -bots 4 examples/pizza.bot.json "$T/pizza.jsonl"
  expect "$LOGS/go_conf.log" '^ботов (\d+), раундов (\d+), горутин (\d+), запросов (\d+), расхождений (\d+),' \
    'g[3] > 0 and g[4] == 0' "Go, соответствие"
  SHOW=1 logged go_race env GOMAXPROCS=$(( $(nproc) > 4 ? $(nproc) : 4 )) $G/gofig-race -rounds 3 -bots 3 examples/pizza.bot.json "$T/small.jsonl"
  expect "$LOGS/go_race.log" '^ботов (\d+), раундов (\d+), горутин (\d+), запросов (\d+), расхождений (\d+),' \
    'g[3] > 0 and g[4] == 0' "Go -race, соответствие"
  for a in "-rounds 0" "-bots 0"; do
    if $G/gofig $a examples/pizza.bot.json "$T/small.jsonl" >/dev/null 2>&1; then
      fail "Go-проверка приняла $a"; fi
  done
  echo "-rounds 0 и -bots 0: Go-проверка красная"
  for k in recovered-broken recovered-false no-id extra-error empty seq-bool null-snapshot; do
    if $G/gofig -corrupt $k examples/pizza.bot.json "$T/small.jsonl" >/dev/null 2>&1; then
      fail "Go-проверка не заметила порчу $k"; fi
  done
  echo "7 видов порчи ответов: Go-проверка красная"
  logged go_reload $G/gofig -reload 5000 examples/pizza.bot.json "$T/small.jsonl"
  # Один словарь запроса из многих горутин: Request не должен его изменять.
  (cd $G && CC=${CC:-gcc} GOMAXPROCS=4 go test -race -count=1 -v \
      -run '^(TestSharedRequestMap|TestAuditHelloAlwaysError|TestAuditRequestBadOp|TestAuditLoadNUL|TestAuditRequestNUL|TestFixLazyLoadWithoutInit|TestFixLoadReason|TestFixRequestTooLarge|TestFixCheckReplyProtocolChanges)$' .) \
      > "$LOGS/go_shared_map.log" 2>&1 \
    || { grep -m3 -A3 "DATA RACE" "$LOGS/go_shared_map.log" || tail -20 "$LOGS/go_shared_map.log"; fail "гонка при общем словаре запроса"; }
  for t in TestSharedRequestMap TestAuditHelloAlwaysError TestAuditRequestBadOp TestAuditLoadNUL TestAuditRequestNUL \
           TestFixLazyLoadWithoutInit TestFixLoadReason TestFixRequestTooLarge TestFixCheckReplyProtocolChanges; do
    grep -q "^--- PASS: $t " "$LOGS/go_shared_map.log" \
      || { tail -5 "$LOGS/go_shared_map.log"; fail "тест $t не выполнялся или не прошёл"; }
  done
  echo "общий словарь запроса из 8 горутин: гонок нет; регрессионные тесты обёртки Bot (hello, op, NUL, ленивый Load, причина отказа, too_large, коды ошибок): пройдены"
  # Под RLIMIT_AS (P-09) рантайм Lean запускается, Load и запрос работают (без -race: TSan занимает адресное пространство).
  (cd $G && CC=${CC:-gcc} go test -count=1 -v -run '^TestFixInitUnderRlimitAS$' .) > "$LOGS/go_rlimit.log" 2>&1 \
    || { tail -20 "$LOGS/go_rlimit.log"; fail "Go: под RLIMIT_AS рантайм Lean не запускается или роняет процесс"; }
  grep -q "^--- PASS: TestFixInitUnderRlimitAS " "$LOGS/go_rlimit.log" || { tail -5 "$LOGS/go_rlimit.log"; fail "тест TestFixInitUnderRlimitAS не выполнялся"; }
  echo "Go под RLIMIT_AS (занято + 256 МиБ, + 1 ГиБ, 3 ГБ): рантайм запускается, Load и запрос работают"
  # Повреждённый сценарий рядом с корректным обязан провалить Go-проверку.
  (head -1 "$T/small.jsonl"; echo '{oops') > "$T/corrupt.jsonl"
  if $G/gofig examples/pizza.bot.json "$T/corrupt.jsonl" >/dev/null 2>&1; then
    fail "Go-проверка пропустила повреждённый сценарий"
  else echo "повреждённый сценарий: Go-проверка красная"; fi
else
  case ",${FIGRAM_WAIVE:-}," in
    *,go,*) echo; echo "== Go: ПРОПУЩЕНО (go не установлен, FIGRAM_WAIVE=go)"; WAIVED+=("Go-часть не выполнялась: go не установлен");;
    *) fail "go не установлен: Go-часть проверки обязательна (явный пропуск — FIGRAM_WAIVE=go, код выхода 3)";;
  esac
fi

if [ ${#WAIVED[@]} -gt 0 ]; then
  echo; echo "ПРОЙДЕНО С ОТСТУПЛЕНИЯМИ (это НЕ «ВСЁ В ПОРЯДКЕ»):"; printf '  - %s\n' "${WAIVED[@]}"
  exit 3
fi
echo; echo "ВСЁ В ПОРЯДКЕ"

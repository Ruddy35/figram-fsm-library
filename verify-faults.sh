#!/usr/bin/env bash
# Самопроверка шлюза: каждая внедрённая неисправность обязана сделать проверку
# красной (код 1), прогон без неисправностей — зелёным (код EXPECT_BASE, по умолчанию 0).
#
#   ./verify-faults.sh prebuilt [НЕИСПРАВНОСТЬ…]   — verify-prebuilt.sh на bin/ (Lean не нужен)
#   ./verify-faults.sh full     [НЕИСПРАВНОСТЬ…]   — verify.sh (нужен Lean; .lake копируется)
#
# Каждая неисправность внедряется в отдельную копию дерева GATE_TREE (по умолчанию —
# это дерево), сама проверка выполняется в копии. Переменные:
#   GATE_TREE         — дерево, чей шлюз проверяется (например, распакованная базовая версия);
#   LAKE_CACHE        — каталог .lake для копий в режиме full (по умолчанию GATE_TREE/.lake);
#   EXPECT_BASE       — ожидаемый код прогона без неисправностей (verify.sh с
#                       FIGRAM_SHA256SUMS даёт 3);
#   FAULT_LOGS        — куда складывать журналы (по умолчанию ./verify-logs/faults).
# Код выхода 0 — все ожидания выполнены.
set -euo pipefail
shopt -s inherit_errexit
export LC_ALL=C.UTF-8
HERE=$(cd "$(dirname "$0")" && pwd)
MODE=${1:?режим: prebuilt или full}; shift
TREE=$(cd "${GATE_TREE:-$HERE}" && pwd)
OUT=${FAULT_LOGS:-$HERE/verify-logs/faults}
mkdir -p "$OUT"
case $MODE in
  prebuilt) SCRIPT=./verify-prebuilt.sh
            ALL="none golden-truncated selftest-shrunk fixtures-shrunk random-docs-shrunk json-fuzz-shrunk mutants-shrunk bin-swapped";;
  full)     SCRIPT=./verify.sh
            ALL="none golden-truncated sums-tampered bin-swapped custom-axiom native-decide theorem-weakened go-missing race-test-not-run coverage-drop coverage-drop-regen engine-bug-golden-edited";;
  *) echo "неизвестный режим $MODE"; exit 2;;
esac
FAULTS=${*:-$ALL}

# sub ФАЙЛ СТАРОЕ НОВОЕ — точная замена ровно одного вхождения.
sub() {
  python3 - "$@" <<'PY' || exit 2
import sys
p, a, b = sys.argv[1:4]
s = open(p, encoding="utf-8").read()
if s.count(a) != 1:
    sys.exit(f"внедрение: в {p} ожидалось ровно одно вхождение {a!r}, найдено {s.count(a)}")
open(p, "w", encoding="utf-8").write(s.replace(a, b))
PY
}

inject() {  # inject НЕИСПРАВНОСТЬ — в текущем каталоге (копии)
  local P=ffi/python-example
  case $1 in
    none) ;;
    golden-truncated)     # 10 из 12 фиксированных сценариев удалены (таймеры, восстановление…)
      head -2 examples/golden.jsonl > examples/g.tmp && mv examples/g.tmp examples/golden.jsonl;;
    sums-tampered)        # эталон хешей испорчен
      local f=${FIGRAM_SHA256SUMS:-bin/SHA256SUMS}
      awk 'NR==1{c=substr($0,1,1); $0=(c=="0"?"1":"0") substr($0,2)}1' "$f" > sums.tampered
      cmp -s "$f" sums.tampered && { echo "внедрение sums-tampered не изменило файл"; exit 2; }
      if [ -n "${FIGRAM_SHA256SUMS:-}" ]; then export FIGRAM_SHA256SUMS=$PWD/sums.tampered; else cp sums.tampered bin/SHA256SUMS; fi;;
    bin-swapped)          # bin/figram-engine подменён, bin/SHA256SUMS не тронут
      cp bin/figram bin/figram-engine;;
    selftest-shrunk)      # самопроверка обвязки проверяет 1 вид порчи вместо 11
      sub $P/harness_selftest.py '    for name, code in CORRUPT.items():' '    for name, code in list(CORRUPT.items())[:1]:';;
    fixtures-shrunk)      # 2 граничных снимка вместо 24
      sub $P/snapshot_fixtures.py 'engine, doc, schema_path = sys.argv[1:4]' 'FIXTURES = FIXTURES[:2]
engine, doc, schema_path = sys.argv[1:4]';;
    random-docs-shrunk)   # 1 случайный документ вместо 100
      sub $P/random_docs.py '    for seed in range(count):' '    for seed in range(min(count, 1)):';;
    json-fuzz-shrunk)     # 10 случаев фаззинга вместо 30000
      sub $P/json_fuzz.py '    for i in range(n):' '    for i in range(min(n, 10)):';;
    mutants-shrunk)       # по 1 мутации на документ вместо 56 и 51
      sub $P/mutation.py '        if len(mutants) < MIN_MUTANTS:' '        mutants = mutants[:1]
        if len(mutants) < MIN_MUTANTS:';;
    custom-axiom)         # теорема из списка опирается на собственную аксиому
      sub Figram/CoreProofs.lean 'theorem step_guard_local (m' 'theorem step_guard_local_orig (m'
      sub Figram/CoreProofs.lean $'end Machine\nend Figram' $'axiom figram_cheat (m : Machine) (s : Snapshot) (ev : Event) (env₁ env₂ : HostEnv)\n    (h : ∀ n ∈ m.requiredGuards s.state ev.key, env₁ n = env₂ n) :\n    m.step s ev env₁ = m.step s ev env₂\n\ntheorem step_guard_local (m : Machine) (s : Snapshot) (ev : Event) (env₁ env₂ : HostEnv)\n    (h : ∀ n ∈ m.requiredGuards s.state ev.key, env₁ n = env₂ n) :\n    m.step s ev env₁ = m.step s ev env₂ := figram_cheat m s ev env₁ env₂ h\n\nend Machine\nend Figram';;
    native-decide)        # доказательство вычислением в компилированном коде (аксиома ofReduceBool)
      printf '\nexample : (20 : Nat) + 22 = 42 := by native_decide\n' >> Figram/Examples.lean;;
    theorem-weakened)     # формулировка ослаблена: недостижимая посылка, доказательство то же
      sub Figram/EngineProofs.lean 'theorem reply_readable (m : Machine) (hwf' 'theorem reply_readable (m : Machine) (_hfalse : False) (hwf';;
    go-missing)           # go не установлен
      export PATH=$(printf '%s' "$PATH" | tr ':' '\n' | while read -r d; do [ -x "$d/go" ] || printf '%s:' "$d"; done | sed 's/:$//')
      command -v go >/dev/null && { echo "внедрение go-missing: go всё ещё в PATH"; exit 2; } || true;;
    race-test-not-run)    # тест гонки переименован и не запускается
      sub ffi/go-example/race_test.go 'func TestSharedRequestMap(' 'func testSharedRequestMap(';;
    coverage-drop)        # генератор сценариев почти не выбирает события сообщений
      sub Figram/Trace.lean '  if roll < 23 then return (.named "unknown:event", g)' '  if roll < 97 then return (.named "unknown:event", g)';;
    coverage-drop-regen)  # то же, но bin/ и bin/SHA256SUMS пересобраны из изменённых исходников
                          # (так выглядит обычный коммит с изменением генератора)
      inject coverage-drop
      lake build >/dev/null 2>&1 || { echo "внедрение: lake build"; exit 2; }
      for b in figram figram-engine; do cp .lake/build/bin/$b bin/$b; strip bin/$b; done
      (cd bin && sha256sum figram figram-engine > SHA256SUMS)
      unset FIGRAM_SHA256SUMS;;
    engine-bug-golden-edited)  # движок перестал выставлять recovered; golden «поправлен» под движок,
                               # bin/ пересобран (обычный коммит: исходники + bin/ + сценарии)
      sub Figram/Engine.lean '    ++ field "recovered" recovered (.bool true))' '    ++ field "recovered" (recovered && as.isEmpty) (.bool true))'
      sed -i 's/,"recovered":true//' examples/golden.jsonl
      lake build >/dev/null 2>&1 || { echo "внедрение: lake build"; exit 2; }
      for b in figram figram-engine; do cp .lake/build/bin/$b bin/$b; strip bin/$b; done
      (cd bin && sha256sum figram figram-engine > SHA256SUMS)
      unset FIGRAM_SHA256SUMS;;
    *) echo "неизвестная неисправность $1"; exit 2;;
  esac
}

ok=1
printf '%-20s %-6s %-8s %s\n' "неисправность" "код" "ожидание" "последняя строка" | tee "$OUT/summary.txt"
for f in $FAULTS; do
  W=$(mktemp -d)
  tar -C "$TREE" --exclude=./.git --exclude=./.lake --exclude=./verify-logs -cf - . | tar -xf - -C "$W"
  if [ "$MODE" = full ]; then cp -a "${LAKE_CACHE:-$TREE/.lake}" "$W/.lake"; fi
  set +e
  ( cd "$W" && inject "$f" && exec timeout 1500 "$SCRIPT" ) > "$OUT/$MODE-$f.log" 2>&1
  rc=$?
  set -e
  want=1; [ "$f" = none ] && want=${EXPECT_BASE:-0}
  verdict="ok"; [ "$rc" -eq "$want" ] || { verdict="НЕТ"; ok=0; }
  last=$(grep -v '^\s*$' "$OUT/$MODE-$f.log" | grep -v '^   (' | tail -1)
  last=${last:0:110}
  printf '%-20s %-6s %-8s %s %s\n' "$f" "$rc" "$want" "$verdict" "$last" | tee -a "$OUT/summary.txt"
  rm -rf "$W"
done
[ $ok = 1 ] && echo "шлюз красный на каждой неисправности и зелёный без них" || { echo "НЕТ: шлюз пропускает неисправности (см. $OUT/summary.txt)"; exit 1; }

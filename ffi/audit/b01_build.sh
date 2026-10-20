#!/usr/bin/env bash
# Регрессионный тест B-01: ffi/build.sh — настоящая сборка продукта на ЭТОМ тулчейне Lean
# (официальный релиз или сборка из исходников gcc/libstdc++) без тестовых адаптаций:
#   1) ffi/build.sh завершается с кодом 0;
#   2) пишет ffi/out/LDFLAGS (полные флаги хоста) и ffi/out/LDFLAGS.runtime (без libfigram.a);
#   3) в ffi/out/lean нет пустых архивов-заглушек;
#   4) хост на C компонуется ТОЛЬКО флагами из ffi/out/LDFLAGS и работает;
#   5) ffi/go-example собирается обычным `go build` без CGO_LDFLAGS и работает;
#   6) C/C++-исходники библиотеки компилируются без системных заголовков (-nostdinc + встроенные
#      заголовки clang), как их компилирует leanc официального релиза (если clang установлен).
#   ffi/audit/b01_build.sh   — код 0, только если все пункты выполнены.
set -u
cd "$(dirname "$0")/../.."
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
fail=0
ok() { echo "ok   $*"; }
bad() { echo "FAIL $*"; fail=1; }
if bash ffi/build.sh > "$T/build.log" 2>&1; then ok "ffi/build.sh rc=0"; else bad "ffi/build.sh rc≠0:"; tail -5 "$T/build.log" | sed 's/^/    /'; fi
for f in LDFLAGS LDFLAGS.runtime; do
  if [ -s "ffi/out/$f" ]; then ok "ffi/out/$f: $(cat ffi/out/$f)"; else bad "ffi/out/$f отсутствует или пуст"; fi
done
for a in ffi/out/lean/*.a; do
  [ -e "$a" ] || continue
  if [ "$(ar t "$a" 2>/dev/null | wc -l)" -gt 0 ]; then ok "архив $(basename "$a"): $(ar t "$a" | wc -l) объектов"
  else bad "архив $(basename "$a") пуст (заглушка)"; fi
done
if [ -s ffi/out/LDFLAGS ]; then
  if ${CC:-cc} -O1 -o "$T/host" ffi/c_harness.c ffi/c_harness_io.c $(cat ffi/out/LDFLAGS) 2> "$T/link.log"; then
    ok "C-хост скомпонован флагами ffi/out/LDFLAGS"
    if "$T/host" examples/pizza.bot.json > "$T/host.out" 2>&1; then ok "C-хост: $(grep -m1 '16 потоков' "$T/host.out")"
    else bad "C-хост завершился с кодом ≠ 0"; tail -5 "$T/host.out" | sed 's/^/    /'; fi
    echo "     ldd C-хоста: $(ldd "$T/host" | awk '{print $1}' | tr '\n' ' ')"
  else bad "C-хост не компонуется флагами ffi/out/LDFLAGS"; grep -m5 -E "error|undefined" "$T/link.log" | sed 's/^/    /'; fi
fi
if command -v go > /dev/null; then
  if (cd ffi/go-example && env -u CGO_LDFLAGS CC=${CC:-gcc} go build -o "$T/gofig" .) > "$T/go.log" 2>&1; then
    ok "go build без CGO_LDFLAGS"
    .lake/build/bin/figram trace examples/pizza.bot.json 20 30 7 > "$T/small.jsonl" 2>/dev/null
    if "$T/gofig" examples/pizza.bot.json "$T/small.jsonl" > "$T/gofig.out" 2>&1; then ok "Go: $(tail -1 "$T/gofig.out")"
    else bad "Go-пример завершился с кодом ≠ 0"; tail -5 "$T/gofig.out" | sed 's/^/    /'; fi
    echo "     ldd Go: $(ldd "$T/gofig" | awk '{print $1}' | tr '\n' ' ')"
  else bad "go build без CGO_LDFLAGS не собирается"; grep -m5 -E "undefined|cannot find|error" "$T/go.log" | sed 's/^/    /'; fi
else echo "skip Go: go не установлен"; fi
if command -v clang > /dev/null; then
  RES=$(clang -print-resource-dir)/include
  P=$(lean --print-prefix)
  for s in ffi/*.c ffi/*.cpp; do
    [ -e "$s" ] || continue
    case "$s" in ffi/c_harness*) continue;; esac
    if clang -nostdinc -isystem "$RES" -I "$P/include" -fPIC -c -o "$T/nostdinc.o" "$s" 2> "$T/nostdinc.log"; then
      ok "$s компилируется без системных заголовков (как leanc официального релиза)"
    else bad "$s не компилируется с -nostdinc:"; head -5 "$T/nostdinc.log" | sed 's/^/    /'; fi
  done
else echo "skip -nostdinc: clang не установлен"; fi
exit $fail

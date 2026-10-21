#!/usr/bin/env bash
# ffi/build.sh is the real product build on THIS Lean toolchain (official release or built from
# source with gcc/libstdc++), without test-only adaptations:
#   1) ffi/build.sh exits with code 0;
#   2) it writes ffi/out/LDFLAGS (complete host flags) and ffi/out/LDFLAGS.runtime (without libfigram.a);
#   3) ffi/out/lean contains no empty stub archives;
#   4) a C host links with ONLY the flags from ffi/out/LDFLAGS and works;
#   5) clients/go builds with a plain `go build` without CGO_LDFLAGS and works;
#   6) the C/C++ sources of the library compile without system headers (-nostdinc + clang's
#      built-in headers), as leanc of the official release compiles them (if clang is installed).
#   tests/capi/build_check.sh   — exit code 0 only if all of the above hold.
set -u
cd "$(dirname "$0")/../.."
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
fail=0
ok() { echo "ok   $*"; }
bad() { echo "FAIL $*"; fail=1; }
if bash ffi/build.sh > "$T/build.log" 2>&1; then ok "ffi/build.sh rc=0"; else bad "ffi/build.sh rc≠0:"; tail -5 "$T/build.log" | sed 's/^/    /'; fi
for f in LDFLAGS LDFLAGS.runtime; do
  if [ -s "ffi/out/$f" ]; then ok "ffi/out/$f: $(cat ffi/out/$f)"; else bad "ffi/out/$f is missing or empty"; fi
done
for a in ffi/out/lean/*.a; do
  [ -e "$a" ] || continue
  if [ "$(ar t "$a" 2>/dev/null | wc -l)" -gt 0 ]; then ok "archive $(basename "$a"): $(ar t "$a" | wc -l) objects"
  else bad "archive $(basename "$a") is empty (a stub)"; fi
done
if [ -s ffi/out/LDFLAGS ]; then
  if ${CC:-cc} -O1 -pthread -o "$T/host" tests/capi/stress.c $(cat ffi/out/LDFLAGS) 2> "$T/link.log"; then
    ok "C host linked with the flags of ffi/out/LDFLAGS"
    if "$T/host" examples/pizza.bot.json 4 2000 1 > "$T/host.out" 2>&1; then ok "C host: $(tail -1 "$T/host.out")"
    else bad "C host exited with a code ≠ 0"; tail -5 "$T/host.out" | sed 's/^/    /'; fi
    echo "     ldd of the C host: $(ldd "$T/host" | awk '{print $1}' | tr '\n' ' ')"
  else bad "C host does not link with the flags of ffi/out/LDFLAGS"; grep -m5 -E "error|undefined" "$T/link.log" | sed 's/^/    /'; fi
fi
if command -v go > /dev/null; then
  if (cd clients/go && env -u CGO_LDFLAGS CC=${CC:-gcc} go build -o "$T/gofig" .) > "$T/go.log" 2>&1; then
    ok "go build without CGO_LDFLAGS"
    [ -x .lake/build/bin/figram ] || lake build figram > "$T/lake.log" 2>&1   # the scenarios come from the CLI
    .lake/build/bin/figram trace examples/pizza.bot.json 20 30 7 > "$T/small.jsonl" 2>/dev/null
    if "$T/gofig" examples/pizza.bot.json "$T/small.jsonl" > "$T/gofig.out" 2>&1; then ok "Go: $(tail -1 "$T/gofig.out")"
    else bad "gofig exited with a code ≠ 0"; tail -5 "$T/gofig.out" | sed 's/^/    /'; fi
    echo "     ldd of gofig: $(ldd "$T/gofig" | awk '{print $1}' | tr '\n' ' ')"
  else bad "go build without CGO_LDFLAGS fails"; grep -m5 -E "undefined|cannot find|error" "$T/go.log" | sed 's/^/    /'; fi
else echo "skip Go: go is not installed"; fi
if command -v clang > /dev/null; then
  P=$(lean --print-prefix)
  # The official Lean release keeps clang's builtin headers in its prefix (leanc passes
  # -isystem $P/include/clang); the resource dir of its bin/clang has no headers.
  if [ -d "$P/include/clang" ]; then RES=$P/include/clang; else RES=$(clang -print-resource-dir)/include; fi
  for s in ffi/*.c ffi/*.cpp; do
    [ -e "$s" ] || continue
    if clang -nostdinc -isystem "$RES" -I "$P/include" -fPIC -c -o "$T/nostdinc.o" "$s" 2> "$T/nostdinc.log"; then
      ok "$s compiles without system headers (like leanc of the official release)"
    else bad "$s does not compile with -nostdinc:"; head -5 "$T/nostdinc.log" | sed 's/^/    /'; fi
  done
else echo "skip -nostdinc: clang is not installed"; fi
exit $fail

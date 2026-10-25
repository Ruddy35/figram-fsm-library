#!/usr/bin/env bash
# The full check of the project (docs/testing.md).
# Exit status: 0 — ALL steps ran and passed, no waivers ("ALL CHECKS PASSED");
#              1 — failure: any step, a checksum or scenario mismatch, a foreign axiom,
#                  a changed theorem statement, zero checks, incomplete coverage;
#              3 — every step that ran passed, but EXPLICITLY allowed waivers were used
#                  (this is not a pass; CI must treat exit status 3 as a failure):
#   FIGRAM_WAIVE=go         — allow a missing go (the Go part does not run).
# FIGRAM_SHA256SUMS=FILE: the stripped rebuild of figram and figram-engine must match FILE
# (`sha256sum` lines for exactly these two names; the release workflow passes its checksums);
# a mismatch is a failure (exit 1). Without it the comparison is skipped, which is not a waiver.
set -euo pipefail
shopt -s inherit_errexit
[ -z "${FIGRAM_SHA256SUMS:-}" ] || FIGRAM_SHA256SUMS=$(realpath "$FIGRAM_SHA256SUMS")
cd "$(dirname "$0")"
F=.lake/build/bin/figram
E=.lake/build/bin/figram-engine
P=tests/engine
# Lower bounds of the number of checks (values of the baseline; fewer is a failure).
MIN_MUTANTS=118        # mutations in the two example documents
MIN_SELFTEST_OK=39     # passed checks of the harness self-test
WAIVED=()
fail() { echo "FAIL: $*"; exit 1; }
python3 -c "import jsonschema" 2>/dev/null || fail "the Python package jsonschema is required: pip install jsonschema"
T=$(mktemp -d)
trap 'rm -rf "$T"' EXIT
LOGS="$(pwd)/verify-logs"
rm -rf "$LOGS"; mkdir -p "$LOGS"
# logged NAME COMMAND…: the full output of the step goes to verify-logs/NAME.log; on success
# the summary (the last lines) is printed, on failure all FAIL lines and the tail of the log.
# clean NAME COMMAND…: like logged, but stderr is kept separately and must be empty.
clean() {
  local name=$1; shift
  if ! "$@" > "$LOGS/$name.log" 2> "$LOGS/$name.err"; then
    echo "FAIL: step '$name' failed, log: verify-logs/$name.log"
    grep -E "^ *FAIL" "$LOGS/$name.log" | head -40 || true
    tail -25 "$LOGS/$name.log"; tail -10 "$LOGS/$name.err"; exit 1
  fi
  if [ -s "$LOGS/$name.err" ]; then
    echo "FAIL: step '$name' prints to stderr:"; head -10 "$LOGS/$name.err"; exit 1
  fi
  tail -1 "$LOGS/$name.log"
}
logged() {
  local name=$1; shift
  if "$@" > "$LOGS/$name.log" 2>&1; then
    tail -${SHOW:-1} "$LOGS/$name.log"
  else
    local rc=$?
    echo "FAIL: step '$name' failed (exit code $rc), log: verify-logs/$name.log"
    grep -E "^ *FAIL|MISMATCH|Traceback|survived:|disagree:" "$LOGS/$name.log" | head -40 || true
    echo "--- log tail:"; tail -25 "$LOGS/$name.log"
    exit 1
  fi
}
# expect FILE REGEX CONDITION DESCRIPTION: the file has a line matching the regular expression
# and the condition (a Python expression over the numbers of the groups g[0], g[1], …) holds;
# otherwise the check fails.
expect() {
  python3 - "$@" <<'PY' || exit 1
import re, sys
path, rx, cond, what = sys.argv[1:5]
text = open(path, encoding="utf-8", errors="replace").read()
m = re.search(rx, text, re.M)
if not m:
    print(f"FAIL: {what}: no line matching '{rx}' in the output"); sys.exit(1)
g = [int(re.sub(r"[^0-9-]", "", x)) for x in m.groups()]
if not eval(cond, {}, {"g": g}):
    print(f"FAIL: {what}: '{m.group(0)}' does not satisfy {cond}"); sys.exit(1)
PY
}
# ccstep NAME COMMAND…: a C build; the compiler diagnostics are saved and printed on failure.
ccstep() {
  local name=$1; shift
  "$@" 2> "$LOGS/$name.cc.log" || { cat "$LOGS/$name.cc.log"; fail "$name does not build"; }
}
step() { echo; echo "== $*"; }

step "Provenance of the run"
echo "date: $(date -u +%FT%TZ); cores: $(nproc)"
if git rev-parse HEAD >/dev/null 2>&1; then
  dirty=$(git status --porcelain --untracked-files=no | wc -l)
  echo "commit: $(git rev-parse HEAD); modified tracked files: $dirty"
else
  echo "commit: not a git repository"
fi
echo "verify.sh sha256: $(sha256sum verify.sh | cut -d' ' -f1)"
echo "lean: $(lean --version)"
echo "python: $(python3 --version 2>&1); jsonschema $(python3 -c 'import importlib.metadata as m; print(m.version("jsonschema"))')"
if command -v go >/dev/null; then echo "go: $(go version)"; else echo "go: not installed"; fi

step "Build without warnings"
lake build 2>&1 | tee "$T/build.log" | tail -1
grep -q "^Build completed successfully" "$T/build.log" || fail "lake build did not report a successful build"
# A Lean/Lake warning is a line "warning: file:…" (or "file:…: warning:") and a task "⚠ [i/n]".
# Not just the word "warning": the module Figram.Warnings prints as "✔ [i/n] Built Figram.Warnings".
WARN_RX='(^|[[:space:]:])warning:|^⚠'
if grep -qiE "$WARN_RX" "$T/build.log"; then grep -iE -A3 "$WARN_RX" "$T/build.log"; fail "build warnings"; fi

step "Binaries built from these sources match FIGRAM_SHA256SUMS"
if [ -n "${FIGRAM_SHA256SUMS:-}" ]; then
  SUMS=$FIGRAM_SHA256SUMS
  [ -f "$SUMS" ] || fail "FIGRAM_SHA256SUMS=$SUMS: no such file"
  [ "$(awk '{print $2}' "$SUMS" | sort | tr '\n' ' ')" = "figram figram-engine " ] \
    || fail "$SUMS: expected exactly two entries, figram and figram-engine"
  for b in figram figram-engine; do cp .lake/build/bin/$b "$T/$b"; strip "$T/$b"; done
  if ! (cd "$T" && sha256sum figram figram-engine) | diff - "$SUMS"; then
    fail "the rebuilt binaries do not match $SUMS"
  fi
  echo "ok: the stripped rebuild matches $SUMS byte for byte"
else
  echo "skipped: FIGRAM_SHA256SUMS is not set, nothing to compare the rebuild with (not a waiver)"
fi

step "Pinned theorems: standard axioms only, statements pinned"
# The theorems that docs/proofs.md calls proved are listed in tests/proofs/theorems.lean.
lake env lean tests/proofs/theorems.lean > "$T/ax.log" 2>&1 || { cat "$T/ax.log"; fail "lean failed on tests/proofs/theorems.lean"; }
grep -v '^STMT ' "$T/ax.log" || true
python3 - "$T/ax.log" tests/proofs/theorems.lean <<'PY' || exit 1
import re, sys
lines = open(sys.argv[1], encoding="utf-8").read().splitlines()
src = open(sys.argv[2], encoding="utf-8").read()
listed = re.findall(r"^#print axioms (\S+)$", src, re.M)
stated = re.findall(r"^\s*``(\S+?),?$", src, re.M)
ALLOWED = {"propext", "Classical.choice", "Quot.sound"}
seen, stmts, bad = {}, set(), []
if not listed or sorted(listed) != sorted(stated):
    bad.append(f"the theorem lists in tests/proofs/theorems.lean differ: {sorted(set(listed) ^ set(stated))}")
for line in lines:
    m = re.fullmatch(r"'([^']+)' depends on axioms: \[(.*)\]", line)
    if m:
        seen[m.group(1)] = {a.strip() for a in m.group(2).split(",") if a.strip()}
    elif re.fullmatch(r"'([^']+)' does not depend on any axioms", line):
        seen[line.split("'")[1]] = set()
    elif line.startswith("STMT "):
        stmts.add(line.split()[1])
    elif line.strip():
        bad.append(f"unexpected lean output: {line}")
for t in listed:
    if t not in seen:
        bad.append(f"{t}: no #print axioms line")
    elif seen[t] - ALLOWED:
        bad.append(f"{t}: disallowed axioms {sorted(seen[t] - ALLOWED)}")
    if t not in stmts:
        bad.append(f"{t}: no statement (STMT)")
for b in bad:
    print("FAIL:", b)
if not bad:
    print(f"theorems {len(listed)}: axioms only from {sorted(ALLOWED)}")
sys.exit(1 if bad else 0)
PY
grep '^STMT ' "$T/ax.log" > "$T/statements.txt" || true
if ! diff tests/proofs/THEOREMS.expected "$T/statements.txt" > "$T/statements.diff"; then
  head -40 "$T/statements.diff"
  fail "theorem statements differ from tests/proofs/THEOREMS.expected (change a statement only deliberately, together with that file)"
fi
echo "the statements of $(wc -l < tests/proofs/THEOREMS.expected) theorems match tests/proofs/THEOREMS.expected"
# Constructs that bypass the kernel or replace the proved code at run time.
if grep -nE '^\s*(@\[[^]]*\]\s*)?((private|protected|noncomputable|unsafe)\s+)*axiom\s|native_decide|implemented_by|@\[extern|\bsorry\b|\badmit\b|skipKernelTC|ofReduceBool|ofReduceNat' \
     Figram.lean Main.lean EngineMain.lean Figram/*.lean; then
  fail "forbidden constructs in the Lean sources (axiom, native_decide, implemented_by, extern, sorry, admit, skipKernelTC)"
fi
echo "no axiom, native_decide, implemented_by, extern, sorry or admit in the sources"

step "Strict proof gate: all theorems of the sources and of the environment, statements, leanchecker"
# tests/proofs/strict_gate.sh (docs/proofs.md): #print axioms for ALL theorems of the sources (their
# number is checked), AxiomGate over the environment (theorems, definitions, opaques of the Figram.*
# modules), the pinned statements (Statements.lean), a ban on run_elab/modifyEnv/addDeclCore/unsafe/csimp
# and leanchecker: the kernel checks every declaration again.
SHOW=4 logged proofs bash tests/proofs/strict_gate.sh
grep -q '^STRICT GATE: PASS$' "$LOGS/proofs.log" || fail "the strict proof gate did not report PASS"

step "Golden scenarios match the committed files"
$F golden "$T/golden" > "$T/golden.out"
expect "$T/golden.out" '^scenarios: (\d+)$' "g[0] > 0 and g[0] == $(grep -c . examples/golden.jsonl)" "golden scenarios"
for f in golden.jsonl pizza.bot.json features.bot.json; do
  cmp "$T/golden/$f" "examples/$f" || fail "examples/$f differs from the output of figram golden"
done
echo "ok ($(cat "$T/golden.out"))"

step "Engine conformance: golden and random scenarios"
for d in pizza features; do
  $F trace examples/$d.bot.json 200 40 7 > "$T/$d.jsonl" 2> "$T/$d.cov"
  cat "$T/$d.cov"
  expect "$T/$d.cov" '^arrow variants covered: (\d+) of (\d+)$' 'g[1] > 0 and g[0] == g[1]' "coverage $d"
done
python3 $P/conformance.py $E --golden examples/golden.jsonl \
  examples/pizza.bot.json="$T/pizza.jsonl" examples/features.bot.json="$T/features.jsonl" | tee "$T/conf.out"
expect "$T/conf.out" '^requests (\d+), mismatches (\d+)$' 'g[0] > 0 and g[1] == 0' "conformance"

step "Conformance on 100 random documents"
logged random_docs python3 $P/random_docs.py $F $E 100 12
expect "$LOGS/random_docs.log" '^documents (\d+), requests (\d+), documents with mismatches (\d+)$' \
  'g[0] == 100 and g[1] > 0 and g[2] == 0' "random documents"

step "Mutation testing"
# --mutants/--mutate are the test mode of the engine, only with FIGRAM_ALLOW_MUTANTS=1 (mutation.py sets it).
SHOW=3 logged mutation env FIGRAM_ALLOW_MUTANTS=1 python3 $P/mutation.py $E --golden examples/golden.jsonl \
  examples/pizza.bot.json="$T/pizza.jsonl" examples/features.bot.json="$T/features.jsonl"
expect "$LOGS/mutation.log" '^detected (\d+) of (\d+) mutations' "g[1] >= $MIN_MUTANTS and g[0] == g[1]" "mutations"

step "Host contract"
logged host python3 $P/host_sim.py $E examples/pizza.bot.json
expect "$LOGS/host.log" '^  users (\d+): outcome as expected \(.*\) for (\d+), orders in the CRM (\d+), events processed (\d+) of (\d+) .*lost updates (\d+), actions not run (\d+)$' \
  'g[0] > 0 and g[0] == g[1] and 0 < g[2] <= g[0] and g[3] == g[4] and g[5] == 0 and g[6] == 0' "host contract"
expect "$LOGS/host.log" '^  the test catches the violation: yes(?: \(control attempts: \d+\))?$' 'True' "control host without the contract"

step "CLI: argument errors give a non-zero exit status"
for args in "trace examples/pizza.bot.json abc 1 7" "trace examples/pizza.bot.json -1 1 7" \
            "trace examples/pizza.bot.json 1 0 7" "trace examples/pizza.bot.json 1 1 7 extra"; do
  if $F $args >/dev/null 2>&1; then fail "an error was expected: $args"; fi
done
echo "ok"

step "Huge exponent: a fast controlled rejection"
printf '{"figram":1,"bot":{"initial":"a","children":{"a":{"timers":{"t":1e1000000000}}}}}' > "$T/big.json"
start=$(date +%s)
set +e
timeout 5 $F check "$T/big.json" >/dev/null 2>"$T/big.err"
rc=$?
set -e
# Exactly exit status 1 with a diagnostic is expected; 124 is a timeout, 13x a crash.
if [ "$rc" -ne 1 ] || ! grep -q "exponent out of range" "$T/big.err" || [ $(( $(date +%s) - start )) -ge 5 ]; then
  fail "exit code $rc, stderr: $(head -1 "$T/big.err")"
fi
echo "ok (exit code 1, $(head -1 "$T/big.err"))"

step "Engine memory: starts under ulimit -v 32, 64 and 256 MiB, address space ≤ 64 MiB, in use ≤ 16 MiB, one thread, stack overflow message"
logged engine_memory python3 $P/engine_memory.py $E examples/pizza.bot.json
expect "$LOGS/engine_memory.log" '^engine memory: address space (\d+) MiB, in use (\d+) MiB, threads (\d+)$' \
  'g[0] <= 64 and g[1] <= 16 and g[2] == 1' "engine memory and threads"
expect "$LOGS/engine_memory.log" '^ok   ulimit -v 32 MiB: exit code 0, replies 2$' 'True' "engine under ulimit -v 32 MiB"

step "Snapshots and protocol edges: schema and engine agree, seq boundary; restart, conditions, tokens, hello; time linear in the snapshot size"
SHOW=2 logged snapshots python3 $P/snapshot_fixtures.py $E examples/features.bot.json schemas/snapshot.schema.json
expect "$LOGS/snapshots.log" '^snapshots (\d+), replies (\d+), schema/engine mismatches (\d+)$' \
  'g[0] >= 30 and g[0] == g[1] and g[2] == 0' "snapshots"
expect "$LOGS/snapshots.log" '^seq boundary: ok$' 'True' "seq boundary"
logged protocol_edges python3 $P/protocol_edges.py $E examples/features.bot.json
expect "$LOGS/protocol_edges.log" '^protocol edge cases (\d+), replies (\d+), mismatches (\d+)$' \
  'g[0] >= 137 and g[0] == g[1] and g[2] == 0' "protocol edge cases"
logged snapshot_scaling python3 $P/snapshot_scaling.py $E examples/features.bot.json
expect "$LOGS/snapshot_scaling.log" '^snapshot scaling: cases (\d+), failed (\d+)$' 'g[0] >= 6 and g[1] == 0' \
  "time linear in the snapshot size"

step "JSON text layer: differential against Python json"
logged json_fuzz python3 $P/json_fuzz.py $F 30000 1
expect "$LOGS/json_fuzz.log" '^cases (\d+): accepted by both (\d+), rejected by both (\d+), mismatches (\d+), outputs not read back (\d+) of (\d+)$' \
  'g[0] == 30000 and g[1] > 0 and g[2] > 0 and g[1] + g[2] == g[0] and g[3] == 0 and g[4] == 0 and g[5] == g[1]' "JSON fuzzing"
cat > "$T/lax.py" <<'PY'
#!/usr/bin/env python3
# Sensitivity control: a "parser" without the restrictions of the format.
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
# The control counts only if the fuzzer failed precisely because of the mismatches it found.
if [ "$rc" -ne 1 ] || ! grep -q "MISMATCH" "$T/lax.out" || grep -q "Traceback" "$T/lax.out"; then
  tail -5 "$T/lax.out"; fail "sensitivity control: the fuzzer did not detect a parser without restrictions (exit code $rc)"
fi
echo "control: the parser without restrictions is detected ($(grep -m1 '^cases' "$T/lax.out"))"

step "C shim: invalid UTF-8, huge inputs, 16 threads, ASan and UBSan (only the shim is instrumented)"
./ffi/build.sh > "$LOGS/ffi_build.log" 2>&1 || { tail -20 "$LOGS/ffi_build.log"; fail "ffi/build.sh"; }
PFX=$(lean --print-prefix)
O=.lake/build/ir/Figram
OBJS=""; for m in Core Chart Compile Lint Format FormatIO Engine Api; do OBJS="$OBJS $O/$m.c.o.export"; done
# ffi/build.sh writes the link flags for this Lean toolchain: the official release uses libc++ from the
# Lean prefix, a build from source the system static libuv/libgmp and libstdc++.
LIBS=$(cat ffi/out/LDFLAGS.runtime)
ccstep ffi_asan gcc -c -g -fsanitize=address,undefined -fexceptions -I"$PFX/include" -o "$T/ffi.o" ffi/figram_ffi.c
ccstep io_asan gcc -c -g -fsanitize=address,undefined -o "$T/io.o" tests/capi/c_harness_io.c
ccstep harness_asan gcc -g -fsanitize=address,undefined -o "$T/harness" tests/capi/c_harness.c "$T/io.o" "$T/ffi.o" ffi/out/figram_guard.o ffi/out/figram_rt.o ffi/out/figram_init.o $OBJS $LIBS
SHOW=2 logged c_asan env ASAN_OPTIONS=detect_leaks=0 UBSAN_OPTIONS=halt_on_error=1 "$T/harness" examples/pizza.bot.json
expect "$LOGS/c_asan.log" '^16 threads × (\d+) calls to one machine: mismatches (\d+)$' 'g[0] > 0 and g[1] == 0' "C, 16 threads"
# Memory across loads and unloads is measured on a plain build (ASan quarantines freed memory).
ccstep harness_plain gcc -O1 -o "$T/harness_plain" tests/capi/c_harness.c tests/capi/c_harness_io.c $(cat ffi/out/LDFLAGS)
logged c_reload "$T/harness_plain" examples/pizza.bot.json memory
expect "$LOGS/c_reload.log" '^(\d+) loads and unloads: memory growth' 'g[0] > 0' "C, loads and unloads"

step "C ABI ffi/figram.h: init without crashing the host, NULL, handles, NUL, UTF-8, size limit, signals"
# tests/capi/regress.sh: every subtest is a separate process; init under RLIMIT_AS (prlimit),
# 2×300 processes with concurrent (including lazy) initialization.
CA="$T/capi"; mkdir -p "$CA"
for t in contract stress; do
  ccstep "$t" gcc -g -O1 -pthread -I"$PFX/include" -o "$CA/$t" "tests/capi/$t.c" $(cat ffi/out/LDFLAGS)
done
ccstep oom gcc -g -O1 -pthread -Wl,--wrap=malloc -o "$CA/oom" tests/capi/oom.c $(cat ffi/out/LDFLAGS)
cp "$T/harness_plain" "$CA/harness_plain"
logged c_contract bash tests/capi/regress.sh "$CA"
expect "$LOGS/c_contract.log" '^C ABI regression checks: (\d+), failed: (\d+)$' 'g[0] >= 45 and g[1] == 0' "C ABI"
# The header with the contract is self-contained for C and C++.
ccstep header_c gcc -std=c99 -Wall -Wextra -Werror -pedantic -fsyntax-only -x c ffi/figram.h
ccstep header_cxx g++ -Wall -Wextra -Werror -fsyntax-only -x c++ ffi/figram.h
echo "ffi/figram.h compiles as C99 and as C++ without warnings"

step "Lean runtime without the libuv event loop: start-up steps and unreachability"
# Self-check of ci/reach.py: code reached only through a pointer table, a constructor or a destructor
# is reachable; code without references is not.
cat > "$T/reach_self.c" <<'SRC'
#define K __attribute__((used, noinline))
volatile int sink, pick;
K static void via_table(void) { sink = 1; }
K static void via_ctor(void) { sink = 2; }
K static void dead(void) { sink = 3; }
static void (*const table[])(void) = {via_table};
__attribute__((constructor)) static void ctor(void) { via_ctor(); }
__attribute__((destructor)) K static void dtor(void) { sink = 4; }
int main(void) { table[pick](); return 0; }
SRC
ccstep reach_self gcc -O1 -fPIE -pie -o "$T/reach_self" "$T/reach_self.c"
logged reach_self python3 ci/reach.py "$T/reach_self" --reachable via_table via_ctor dtor --unreachable dead
# lean_initialize_runtime_module of ffi/figram_init.cpp is the stock one from libleanrt.a of this
# toolchain without initialize_libuv (executables) and without initialize_stack_overflow (library); the
# calls are compared by disassembly, so a new start-up step in another Lean version stops the check.
ar p "$PFX/lib/lean/libleanrt.a" init_module.cpp.o > "$T/init_module.o"
python3 ci/reach.py --calls "$T/init_module.o" lean_initialize_runtime_module > "$T/init.stock"
grep -qx initialize_libuv "$T/init.stock" || fail "the stock runtime start-up does not call initialize_libuv: $(tr '\n' ' ' < "$T/init.stock")"
for b in "$F" "$E" "$CA/contract"; do
  skip=initialize_libuv; [ "$b" = "$CA/contract" ] && skip='initialize_libuv|lean::initialize_stack_overflow\(\)'
  python3 ci/reach.py --calls "$b" lean_initialize_runtime_module > "$T/init.got"
  grep -Evx "$skip" "$T/init.stock" | diff - "$T/init.got" > "$T/init.diff" || fail "$b: the runtime start-up steps are not the stock ones minus the skipped ones: $(cat "$T/init.diff")"
done
echo "runtime start-up steps: the $(wc -l < "$T/init.stock") stock ones without initialize_libuv; the library also without initialize_stack_overflow"
# Reachability in the built files (ci/reach.py: a graph of calls and pointers from main and the
# constructors). Only the lean_uv_* and event_loop_* functions use the event loop (src/runtime/uv/*.cpp
# of Lean 4.35); the static initializer of global_ev only zeroes a field and does not call libuv.
UV='(lean::)?lean_uv_.* (lean::)?event_loop_.* initialize_libuv (lean::)?initialize_libuv_.* (lean::)?g_uv_.*_external_class'
logged reach_engine python3 ci/reach.py "$E" --unreachable $UV --reachable '(lean::)?segv_handler.*'
logged reach_cli python3 ci/reach.py "$F" --unreachable $UV --reachable '(lean::)?segv_handler.*'
logged reach_lib python3 ci/reach.py "$CA/contract" --unreachable $UV '(lean::)?segv_handler.*' \
  'lean::initialize_stack_overflow\(\)' --reachable 'lean::initialize_alloc\(\)'
# Control: the same host with the stock runtime start-up, where all of the above is reachable (the check is not empty).
ccstep contract_stock gcc -O1 -pthread -I"$PFX/include" -o "$T/contract-stock" tests/capi/contract.c \
  ffi/out/figram_ffi.o ffi/out/figram_guard.o ffi/out/figram_rt.o $OBJS $LIBS
logged reach_control python3 ci/reach.py "$T/contract-stock" --reachable $UV '(lean::)?segv_handler.*'

step "Python client: engine failures, method contract, clean stderr"
clean client_faults python3 $P/client_faults.py $E examples/pizza.bot.json
clean client_contract python3 $P/client_contract.py $E examples/features.bot.json
clean client_robustness python3 $P/client_robustness.py $E examples/pizza.bot.json examples/features.bot.json

step "Test harness catches a corrupted, incomplete, empty and hanging engine"
$F trace examples/pizza.bot.json 20 30 7 > "$T/small.jsonl" 2>/dev/null
logged selftest python3 $P/harness_selftest.py $E examples/pizza.bot.json "$T/pizza.jsonl" "$T/features.jsonl"
n_ok=$(grep -c '^ok ' "$LOGS/selftest.log" || true)
n_bad=$(grep -c '^FAIL' "$LOGS/selftest.log" || true)
[ "$n_ok" -ge "$MIN_SELFTEST_OK" ] && [ "$n_bad" -eq 0 ] \
  || fail "harness self-test: passed $n_ok (at least $MIN_SELFTEST_OK needed), failed $n_bad"

step "Load test: 64 threads, 2 processes"
logged load python3 $P/loadtest.py $E examples/pizza.bot.json "$T/pizza.jsonl" 64 2
expect "$LOGS/load.log" '^threads +(\d+), processes (\d+): steps (\d+) .*mismatches (\d+),' \
  'g[0] == 64 and g[2] > 0 and g[3] == 0' "load"

if command -v go >/dev/null; then
  step "Go: embedded engine, scenarios from many goroutines"
  G=clients/go
  (cd $G && CC=${CC:-gcc} go build -ldflags=-extldflags=-Wl,--gc-sections -o gofig .) > "$LOGS/go_build.log" 2>&1 || { cat "$LOGS/go_build.log"; fail "go build"; }
  (cd $G && CC=${CC:-gcc} go build -race -o gofig-race .) > "$LOGS/go_build_race.log" 2>&1 \
    || { cat "$LOGS/go_build_race.log"; fail "go build -race"; }
  SHOW=1 logged go_conf $G/gofig -rounds 3 -bots 4 examples/pizza.bot.json "$T/pizza.jsonl"
  expect "$LOGS/go_conf.log" '^bots (\d+), rounds (\d+), goroutines (\d+), requests (\d+), mismatches (\d+),' \
    'g[3] > 0 and g[4] == 0' "Go, conformance"
  SHOW=1 logged go_race env GOMAXPROCS=$(( $(nproc) > 4 ? $(nproc) : 4 )) $G/gofig-race -rounds 3 -bots 3 examples/pizza.bot.json "$T/small.jsonl"
  expect "$LOGS/go_race.log" '^bots (\d+), rounds (\d+), goroutines (\d+), requests (\d+), mismatches (\d+),' \
    'g[3] > 0 and g[4] == 0' "Go -race, conformance"
  for a in "-rounds 0" "-bots 0"; do
    if $G/gofig $a examples/pizza.bot.json "$T/small.jsonl" >/dev/null 2>&1; then
      fail "the Go check accepted $a"; fi
  done
  echo "-rounds 0 and -bots 0: the Go check is red"
  for k in recovered-broken recovered-false no-id extra-error empty seq-bool null-snapshot; do
    if $G/gofig -corrupt $k examples/pizza.bot.json "$T/small.jsonl" >/dev/null 2>&1; then
      fail "the Go check did not notice the corruption $k"; fi
  done
  echo "7 kinds of corrupted replies: the Go check is red"
  logged go_reload $G/gofig -reload 5000 examples/pizza.bot.json "$T/small.jsonl"
  # One request map used by many goroutines: Request must not modify it.
  (cd $G && CC=${CC:-gcc} GOMAXPROCS=4 go test -race -count=1 -v \
      -run '^(TestSharedRequestMap|TestAuditHelloAlwaysError|TestAuditRequestBadOp|TestAuditLoadNUL|TestAuditRequestNUL|TestFixLazyLoadWithoutInit|TestFixLoadReason|TestFixRequestTooLarge|TestFixCheckReplyProtocolChanges|TestFixStartWithEvent)$' .) \
      > "$LOGS/go_shared_map.log" 2>&1 \
    || { grep -m3 -A3 "DATA RACE" "$LOGS/go_shared_map.log" || tail -20 "$LOGS/go_shared_map.log"; fail "a race on the shared request map"; }
  for t in TestSharedRequestMap TestAuditHelloAlwaysError TestAuditRequestBadOp TestAuditLoadNUL TestAuditRequestNUL \
           TestFixLazyLoadWithoutInit TestFixLoadReason TestFixRequestTooLarge TestFixCheckReplyProtocolChanges TestFixStartWithEvent; do
    grep -q "^--- PASS: $t " "$LOGS/go_shared_map.log" \
      || { tail -5 "$LOGS/go_shared_map.log"; fail "test $t did not run or did not pass"; }
  done
  echo "a request map shared by 8 goroutines: no races; regression tests of the Bot wrapper (hello, op, NUL, lazy Load, failure reason, too_large, error codes): passed"
  # Under RLIMIT_AS the Lean runtime starts, Load and a request work (without -race: TSan takes address space).
  (cd $G && CC=${CC:-gcc} go test -count=1 -v -run '^TestFixInitUnderRlimitAS$' .) > "$LOGS/go_rlimit.log" 2>&1 \
    || { tail -20 "$LOGS/go_rlimit.log"; fail "Go: under RLIMIT_AS the Lean runtime does not start or crashes the process"; }
  grep -q "^--- PASS: TestFixInitUnderRlimitAS " "$LOGS/go_rlimit.log" || { tail -5 "$LOGS/go_rlimit.log"; fail "test TestFixInitUnderRlimitAS did not run"; }
  echo "Go under RLIMIT_AS (in use + 256 MiB, + 1 GiB, 3 GB): the runtime starts, Load and a request work"
  # A corrupted scenario next to a correct one must fail the Go check.
  (head -1 "$T/small.jsonl"; echo '{oops') > "$T/corrupt.jsonl"
  if $G/gofig examples/pizza.bot.json "$T/corrupt.jsonl" >/dev/null 2>&1; then
    fail "the Go check let a corrupted scenario through"
  else echo "corrupted scenario: the Go check is red"; fi
else
  case ",${FIGRAM_WAIVE:-}," in
    *,go,*) echo; echo "== Go: SKIPPED (go is not installed, FIGRAM_WAIVE=go)"; WAIVED+=("the Go part did not run: go is not installed");;
    *) fail "go is not installed: the Go part of the check is required (explicit skip: FIGRAM_WAIVE=go, exit status 3)";;
  esac
fi

if [ ${#WAIVED[@]} -gt 0 ]; then
  echo; echo "PASSED WITH WAIVERS (this is NOT a pass):"; printf '  - %s\n' "${WAIVED[@]}"
  exit 3
fi
echo; echo "ALL CHECKS PASSED"

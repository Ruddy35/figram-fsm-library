#!/usr/bin/env bash
# Checks prebuilt binaries without Lean (Linux x86_64, they depend on libc).
# Covers the engine, the CLI, the client, the test harness, the host contract, the JSON text layer.
# Does NOT cover the build, the theorems, the C shim and Go: they need ./verify.sh with Lean 4.35.0.
# That the binaries were built from these sources is shown only by ./verify.sh with
# FIGRAM_SHA256SUMS (the build is reproducible).
#
#   ./verify-prebuilt.sh DIR          the full check (about 3 min)
#   ./verify-prebuilt.sh --quick DIR  a shortened check (about 1 min): fewer documents and fuzzing cases,
#                                     no mutations and no harness self-test; NOT a replacement
# DIR (or FIGRAM_BIN=DIR) is the directory with `figram` and `figram-engine`, e.g. an unpacked
# release or .lake/build/bin. If DIR/SHA256SUMS exists, `sha256sum -c` must accept it.
#
# Exit status: 0 — the full check passed; 1 — failure; 3 — the shortened check (--quick) passed:
# this is not the full check, CI must treat exit status 3 as a failure; 2 — usage error.
set -euo pipefail
shopt -s inherit_errexit
fail() { echo "FAIL: $*"; exit 1; }
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
MIN_MUTANTS=118        # mutations in the two example documents (fewer is a failure)
MIN_SELFTEST_OK=39     # passed checks of the harness self-test
F=$BIN/figram
E=$BIN/figram-engine
P=tests/engine
echo "binaries: $BIN"
if [ -f "$BIN/SHA256SUMS" ]; then
  (cd "$BIN" && sha256sum -c SHA256SUMS)
else
  echo "no $BIN/SHA256SUMS: checksum check skipped"
fi
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
LAST=$SECONDS
step() { echo "   ($(( SECONDS - LAST )) s)"; LAST=$SECONDS; echo; echo "== $*"; }
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

step "Golden scenarios match the committed files"
$F golden "$T/golden" > "$T/golden.out"
expect "$T/golden.out" '^scenarios: (\d+)$' "g[0] > 0 and g[0] == $(grep -c . examples/golden.jsonl)" "golden scenarios"
for f in golden.jsonl pizza.bot.json features.bot.json; do
  cmp "$T/golden/$f" "examples/$f" || fail "examples/$f differs from the output of figram golden"
done
echo "ok ($(cat "$T/golden.out"))"

step "Engine conformance"
for d in pizza features; do
  $F trace examples/$d.bot.json 200 40 7 > "$T/$d.jsonl" 2> "$T/$d.cov"
  cat "$T/$d.cov"
  expect "$T/$d.cov" '^arrow variants covered: (\d+) of (\d+)$' 'g[1] > 0 and g[0] == g[1]' "coverage $d"
done
python3 $P/conformance.py $E --golden examples/golden.jsonl \
  examples/pizza.bot.json="$T/pizza.jsonl" examples/features.bot.json="$T/features.jsonl" | tee "$T/conf.out"
expect "$T/conf.out" '^requests (\d+), mismatches (\d+)$' 'g[0] > 0 and g[1] == 0' "conformance"
NDOCS=$([ $QUICK = 1 ] && echo 20 || echo 100)
logged random_docs python3 $P/random_docs.py $F $E $NDOCS 12
expect "$LOGS/random_docs.log" '^documents (\d+), requests (\d+), documents with mismatches (\d+)$' \
  "g[0] == $NDOCS and g[1] > 0 and g[2] == 0" "random documents"

if [ $QUICK = 0 ]; then
  step "Mutation testing"
  # --mutants/--mutate are the test mode of the engine, only with FIGRAM_ALLOW_MUTANTS=1 (mutation.py sets it).
  SHOW=3 logged mutation env FIGRAM_ALLOW_MUTANTS=1 python3 $P/mutation.py $E --golden examples/golden.jsonl \
    examples/pizza.bot.json="$T/pizza.jsonl" examples/features.bot.json="$T/features.jsonl"
  expect "$LOGS/mutation.log" '^detected (\d+) of (\d+) mutations' "g[1] >= $MIN_MUTANTS and g[0] == g[1]" "mutations"
fi

step "Engine memory, threads and stack overflow"
logged engine_memory python3 $P/engine_memory.py $E examples/pizza.bot.json
expect "$LOGS/engine_memory.log" '^engine memory: address space (\d+) MiB, in use (\d+) MiB, threads (\d+)$' \
  'g[0] <= 64 and g[1] <= 16 and g[2] == 1' "engine memory and threads"
expect "$LOGS/engine_memory.log" '^ok   ulimit -v 32 MiB: exit code 0, replies 2$' 'True' "engine under ulimit -v 32 MiB"

step "Snapshots, protocol edges and the JSON text layer"
SHOW=2 logged snapshots python3 $P/snapshot_fixtures.py $E examples/features.bot.json schemas/snapshot.schema.json
expect "$LOGS/snapshots.log" '^snapshots (\d+), replies (\d+), schema/engine mismatches (\d+)$' \
  'g[0] >= 30 and g[0] == g[1] and g[2] == 0' "snapshots"
expect "$LOGS/snapshots.log" '^seq boundary: ok$' 'True' "seq boundary"
logged protocol_edges python3 $P/protocol_edges.py $E examples/features.bot.json
expect "$LOGS/protocol_edges.log" '^protocol edge cases (\d+), replies (\d+), mismatches (\d+)$' \
  'g[0] >= 136 and g[0] == g[1] and g[2] == 0' "protocol edge cases"
logged snapshot_scaling python3 $P/snapshot_scaling.py $E examples/features.bot.json
expect "$LOGS/snapshot_scaling.log" '^snapshot scaling: cases (\d+), failed (\d+)$' 'g[0] >= 4 and g[1] == 0' \
  "time linear in the snapshot size"
NFUZZ=$([ $QUICK = 1 ] && echo 5000 || echo 30000)
logged json_fuzz python3 $P/json_fuzz.py $F $NFUZZ 1
expect "$LOGS/json_fuzz.log" '^cases (\d+): accepted by both (\d+), rejected by both (\d+), mismatches (\d+), outputs not read back (\d+) of (\d+)$' \
  "g[0] == $NFUZZ and g[1] > 0 and g[2] > 0 and g[1] + g[2] == g[0] and g[3] == 0 and g[4] == 0 and g[5] == g[1]" "JSON fuzzing"

step "Python client"
clean client_faults python3 $P/client_faults.py $E examples/pizza.bot.json
clean client_contract python3 $P/client_contract.py $E examples/features.bot.json
clean client_robustness python3 $P/client_robustness.py $E examples/pizza.bot.json examples/features.bot.json

if [ $QUICK = 0 ]; then
  step "Harness self-test"
  logged selftest python3 $P/harness_selftest.py $E examples/pizza.bot.json "$T/pizza.jsonl" "$T/features.jsonl"
  n_ok=$(grep -c '^ok ' "$LOGS/selftest.log" || true)
  n_bad=$(grep -c '^FAIL' "$LOGS/selftest.log" || true)
  [ "$n_ok" -ge "$MIN_SELFTEST_OK" ] && [ "$n_bad" -eq 0 ] \
    || fail "harness self-test: passed $n_ok (at least $MIN_SELFTEST_OK needed), failed $n_bad"
fi

step "Host contract and load test"
logged host python3 $P/host_sim.py $E examples/pizza.bot.json
expect "$LOGS/host.log" '^  users (\d+): outcome as expected \(.*\) for (\d+), orders in the CRM (\d+), events processed (\d+) of (\d+) .*lost updates (\d+), actions not run (\d+)$' \
  'g[0] > 0 and g[0] == g[1] and 0 < g[2] <= g[0] and g[3] == g[4] and g[5] == 0 and g[6] == 0' "host contract"
expect "$LOGS/host.log" '^  the test catches the violation: yes(?: \(control attempts: \d+\))?$' 'True' "control host without the contract"
logged load python3 $P/loadtest.py $E examples/pizza.bot.json "$T/pizza.jsonl" 64 2
expect "$LOGS/load.log" '^threads +(\d+), processes (\d+): steps (\d+) .*mismatches (\d+),' \
  'g[0] == 64 and g[2] > 0 and g[3] == 0' "load"
echo "   ($(( SECONDS - LAST )) s)"

echo
if [ $QUICK = 1 ]; then
  echo "QUICK CHECK PASSED: mutations and the harness self-test skipped, 20 documents instead of 100,"
  echo "5000 fuzzing cases instead of 30000. This does not replace the full check (exit status 3)."
  exit 3
else
  echo "PREBUILT CHECK PASSED (the build, the theorems, C and Go were not checked: see ./verify.sh)"
fi

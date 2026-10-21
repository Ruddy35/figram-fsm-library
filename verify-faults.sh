#!/usr/bin/env bash
# Self-test of the gate: every injected fault must make the check red (exit status 1), and the
# run without faults must be green (exit status EXPECT_BASE, 0 by default).
#
#   ./verify-faults.sh prebuilt [FAULT…]   — verify-prebuilt.sh on FIGRAM_BIN (no Lean needed)
#   ./verify-faults.sh full     [FAULT…]   — verify.sh (needs Lean; .lake is copied)
#
# Every fault is injected into a separate copy of the tree GATE_TREE (this tree by default), and
# the check runs in the copy. Variables:
#   GATE_TREE         — the tree whose gate is checked (for example, an unpacked baseline);
#   LAKE_CACHE        — the .lake directory for the copies in full mode (default GATE_TREE/.lake);
#   FIGRAM_BIN        — prebuilt mode: directory with figram and figram-engine (default
#                       GATE_TREE/.lake/build/bin); every copy gets them in prebuilt/ with a
#                       SHA256SUMS of its own;
#   EXPECT_BASE       — the expected exit status of the run without faults (default 0);
#   FAULT_LOGS        — where to put the logs (default ./verify-logs/faults).
# In full mode every copy runs verify.sh with FIGRAM_SHA256SUMS = checksums of the stripped
# binaries built from the unmodified copy, so the checksum step is exercised as in a release.
# Exit status 0: all expectations are met.
set -euo pipefail
shopt -s inherit_errexit
export LC_ALL=C.UTF-8
HERE=$(cd "$(dirname "$0")" && pwd)
MODE=${1:?mode: prebuilt or full}; shift
TREE=$(cd "${GATE_TREE:-$HERE}" && pwd)
OUT=${FAULT_LOGS:-$HERE/verify-logs/faults}
mkdir -p "$OUT"
case $MODE in
  prebuilt) SCRIPT=./verify-prebuilt.sh
            SRC_BIN=$(cd "${FIGRAM_BIN:-$TREE/.lake/build/bin}" && pwd)
            ALL="none golden-truncated selftest-shrunk fixtures-shrunk random-docs-shrunk json-fuzz-shrunk mutants-shrunk bin-swapped";;
  full)     SCRIPT=./verify.sh
            ALL="none golden-truncated sums-tampered build-warning custom-axiom native-decide theorem-weakened go-missing race-test-not-run coverage-drop coverage-drop-regen engine-bug-golden-edited";;
  *) echo "unknown mode $MODE"; exit 2;;
esac
FAULTS=${*:-$ALL}

# sub FILE OLD NEW — exact replacement of exactly one occurrence.
sub() {
  python3 - "$@" <<'PY' || exit 2
import sys
p, a, b = sys.argv[1:4]
s = open(p, encoding="utf-8").read()
if s.count(a) != 1:
    sys.exit(f"injection: expected exactly one occurrence of {a!r} in {p}, found {s.count(a)}")
open(p, "w", encoding="utf-8").write(s.replace(a, b))
PY
}

# sums_of_build — SHA256SUMS lines of the stripped binaries in .lake/build/bin.
sums_of_build() {
  local d; d=$(mktemp -d)
  for b in figram figram-engine; do cp .lake/build/bin/$b "$d/$b"; strip "$d/$b"; done
  (cd "$d" && sha256sum figram figram-engine); rm -rf "$d"
}

# prepare — in the current directory (a copy): the binaries under test and their checksums.
prepare() {
  if [ "$MODE" = prebuilt ]; then
    mkdir -p prebuilt
    cp "$SRC_BIN/figram" "$SRC_BIN/figram-engine" prebuilt/
    (cd prebuilt && sha256sum figram figram-engine > SHA256SUMS)
    export FIGRAM_BIN=$PWD/prebuilt
  else
    lake build >/dev/null 2>&1 || { echo "prepare: lake build"; exit 2; }
    sums_of_build > release.sums
    export FIGRAM_SHA256SUMS=$PWD/release.sums
  fi
}

inject() {  # inject FAULT — in the current directory (a copy)
  local P=tests/engine
  case $1 in
    none) ;;
    golden-truncated)     # 10 of the 12 golden scenarios removed (timers, recovery…)
      head -2 examples/golden.jsonl > examples/g.tmp && mv examples/g.tmp examples/golden.jsonl;;
    sums-tampered)        # the reference checksums are corrupted
      local f=${FIGRAM_SHA256SUMS:-prebuilt/SHA256SUMS}
      awk 'NR==1{c=substr($0,1,1); $0=(c=="0"?"1":"0") substr($0,2)}1' "$f" > sums.tampered
      cmp -s "$f" sums.tampered && { echo "injection sums-tampered did not change the file"; exit 2; }
      if [ -n "${FIGRAM_SHA256SUMS:-}" ]; then export FIGRAM_SHA256SUMS=$PWD/sums.tampered; else cp sums.tampered prebuilt/SHA256SUMS; fi;;
    bin-swapped)          # prebuilt/figram-engine replaced by figram, prebuilt/SHA256SUMS untouched
      [ -d prebuilt ] || { echo "bin-swapped: prebuilt mode only"; exit 2; }
      cp prebuilt/figram prebuilt/figram-engine;;
    selftest-shrunk)      # the harness self-test checks 1 kind of corruption instead of 13
      sub $P/harness_selftest.py '    for name, code in CORRUPT.items():' '    for name, code in list(CORRUPT.items())[:1]:';;
    fixtures-shrunk)      # 2 edge snapshots instead of 24
      sub $P/snapshot_fixtures.py 'engine, doc, schema_path = sys.argv[1:4]' 'FIXTURES = FIXTURES[:2]
engine, doc, schema_path = sys.argv[1:4]';;
    random-docs-shrunk)   # 1 random document instead of 100
      sub $P/random_docs.py '    for seed in range(count):' '    for seed in range(min(count, 1)):';;
    json-fuzz-shrunk)     # 10 fuzzing cases instead of 30000
      sub $P/json_fuzz.py '    for i in range(n):' '    for i in range(min(n, 10)):';;
    mutants-shrunk)       # 1 mutation per document instead of 56 and 51
      sub $P/mutation.py '        if len(mutants) < MIN_MUTANTS:' '        mutants = mutants[:1]
        if len(mutants) < MIN_MUTANTS:';;
    custom-axiom)         # a listed theorem relies on an axiom of its own
      sub Figram/CoreProofs.lean 'theorem step_guard_local (m' 'theorem step_guard_local_orig (m'
      sub Figram/CoreProofs.lean $'end Machine\nend Figram' $'axiom figram_cheat (m : Machine) (s : Snapshot) (ev : Event) (env₁ env₂ : HostEnv)\n    (h : ∀ n ∈ m.requiredGuards s.state ev.key, env₁ n = env₂ n) :\n    m.step s ev env₁ = m.step s ev env₂\n\ntheorem step_guard_local (m : Machine) (s : Snapshot) (ev : Event) (env₁ env₂ : HostEnv)\n    (h : ∀ n ∈ m.requiredGuards s.state ev.key, env₁ n = env₂ n) :\n    m.step s ev env₁ = m.step s ev env₂ := figram_cheat m s ev env₁ env₂ h\n\nend Machine\nend Figram';;
    build-warning)        # a build with a Lean warning (an unused variable)
      printf '\ndef figramFaultUnused (x : Nat) : Nat := let y := 3; x\n' >> Figram/Examples.lean;;
    native-decide)        # a proof by evaluation in compiled code (the ofReduceBool axiom)
      printf '\nexample : (20 : Nat) + 22 = 42 := by native_decide\n' >> Figram/Examples.lean;;
    theorem-weakened)     # the statement is weakened: an unsatisfiable premise, the same proof
      sub Figram/EngineProofs.lean 'theorem reply_readable (m : Machine) (hwf' 'theorem reply_readable (m : Machine) (_hfalse : False) (hwf';;
    go-missing)           # go is not installed: every PATH directory with go or gofmt (on GitHub
                          # runners also /usr/bin) is replaced by a shadow directory that links all
                          # its other entries, so the rest of the tools stay reachable
      local dirs d e n=0 path=""
      IFS=: read -r -a dirs <<< "$PATH"
      for d in "${dirs[@]}"; do
        if [ -n "$d" ] && { [ -e "$d/go" ] || [ -e "$d/gofmt" ]; }; then
          n=$((n + 1)); mkdir -p "$PWD/.no-go-path/$n"
          for e in "$d"/*; do
            case ${e##*/} in go|gofmt) continue;; esac
            if [ -e "$e" ]; then ln -s "$e" "$PWD/.no-go-path/$n/"; fi
          done
          d=$PWD/.no-go-path/$n
        fi
        path=${path:+$path:}$d
      done
      export PATH=$path
      command -v go >/dev/null && { echo "injection go-missing: go is still in PATH"; exit 2; }
      command -v timeout >/dev/null || { echo "injection go-missing: timeout is no longer in PATH"; exit 2; };;
    race-test-not-run)    # the race test is renamed and does not run
      sub clients/go/race_test.go 'func TestSharedRequestMap(' 'func testSharedRequestMap(';;
    coverage-drop)        # the scenario generator almost never picks message events
      sub Figram/Trace.lean '  if roll < 23 then return (.named "unknown:event", g)' '  if roll < 97 then return (.named "unknown:event", g)';;
    coverage-drop-regen)  # the same, but FIGRAM_SHA256SUMS is regenerated from the changed sources
                          # (a release built from a commit that changes the generator)
      inject coverage-drop
      lake build >/dev/null 2>&1 || { echo "injection: lake build"; exit 2; }
      sums_of_build > "$FIGRAM_SHA256SUMS";;
    engine-bug-golden-edited)  # the engine no longer sets recovered; golden is "fixed" to match the engine,
                               # FIGRAM_SHA256SUMS regenerated (a release of sources + scenarios)
      sub Figram/Engine.lean '    ++ field "recovered" recovered (.bool true))' '    ++ field "recovered" (recovered && as.isEmpty) (.bool true))'
      sed -i 's/,"recovered":true//' examples/golden.jsonl
      lake build >/dev/null 2>&1 || { echo "injection: lake build"; exit 2; }
      sums_of_build > "$FIGRAM_SHA256SUMS";;
    *) echo "unknown fault $1"; exit 2;;
  esac
}

ok=1
printf '%-20s %-6s %-8s %s\n' "fault" "exit" "expected" "verdict, last line" | tee "$OUT/summary.txt"
for f in $FAULTS; do
  W=$(mktemp -d)
  tar -C "$TREE" --exclude=./.git --exclude=./.lake --exclude=./verify-logs -cf - . | tar -xf - -C "$W"
  if [ "$MODE" = full ]; then cp -a "${LAKE_CACHE:-$TREE/.lake}" "$W/.lake"; fi
  set +e
  ( cd "$W" && prepare && inject "$f" && exec timeout 1500 "$SCRIPT" ) > "$OUT/$MODE-$f.log" 2>&1
  rc=$?
  set -e
  want=1; [ "$f" = none ] && want=${EXPECT_BASE:-0}
  verdict="ok"; [ "$rc" -eq "$want" ] || { verdict="FAIL"; ok=0; }
  last=$(grep -v '^\s*$' "$OUT/$MODE-$f.log" | grep -v '^   (' | tail -1)
  last=${last:0:110}
  printf '%-20s %-6s %-8s %s %s\n' "$f" "$rc" "$want" "$verdict" "$last" | tee -a "$OUT/summary.txt"
  rm -rf "$W"
done
[ $ok = 1 ] && echo "the gate is red on every fault and green without them" || { echo "FAIL: the gate lets faults through (see $OUT/summary.txt)"; exit 1; }

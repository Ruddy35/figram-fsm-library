#!/usr/bin/env bash
# Runs every formats-correctness test, its strict mode and its deliberately
# broken oracles, logging each run with tests/lib/run_logged.sh.
#   tests/formats/run_all.sh [LOGDIR] [SEEDS]
# LOGDIR defaults to tests/formats/logs (git-ignored).  The binaries under test are
# $FIGRAM and $FIGRAM_ENGINE (default .lake/build/bin/figram and figram-engine).
# Exit code 0 only if every run gave its expected exit code.
# Expected: normal runs exit 0; every --break / mutant run exits 1.  The --strict
# runs count the rows of the defects found during the audit as failures: FC-01 (an
# exponent printed by `fmt` that the parser rejects), FC-06 (control bytes of document
# names in diagnostics) and FC-03 (invalid UTF-8) are fixed, so they exit 0 now (they
# exited 1 on the baseline: commit d69bbaf, the code before the audit fixes; audit
# archive: commit 16c885a).  f-parser-scaling checks time/RSS budgets of the parser;
# f-parser-diff runs only when FIGRAM_OLD names another `figram` to compare with.
set -u
here="$(cd "$(dirname "$0")" && pwd)"
abs() { case $1 in /*) echo "$1";; *) echo "$PWD/$1";; esac; }
L=$(abs "${1:-$here/logs}")
SEEDS="${2:-250}"
FIGRAM=$(abs "${FIGRAM:-$here/../../.lake/build/bin/figram}")
FIGRAM_ENGINE=$(abs "${FIGRAM_ENGINE:-$here/../../.lake/build/bin/figram-engine}")
export FIGRAM FIGRAM_ENGINE
[ -z "${FIGRAM_OLD:-}" ] || FIGRAM_OLD=$(abs "$FIGRAM_OLD")
for b in "$FIGRAM" "$FIGRAM_ENGINE"; do
  [ -x "$b" ] || { echo "not an executable: $b (run lake build or set FIGRAM / FIGRAM_ENGINE)"; exit 2; }
done
R="$here/../lib/run_logged.sh"
T="${TMPDIR:-/tmp}"
fails=0
N="nice -n 10 timeout 900"
mkdir -p "$L"
expect() {  # expect RC NAME CMD...
  local want=$1 name=$2; shift 2
  $R "$L/$name" $N "$@" >/dev/null; local rc=$?
  local verdict=PASS; [ "$rc" = "$want" ] || { verdict=UNEXPECTED; fails=$((fails + 1)); }
  printf '%-34s want=%s got=%s %s  %s\n' "$name" "$want" "$rc" "$verdict" "$(tail -1 "$L/$name.stdout")"
}
cd "$here/../.."
expect 0 a-roundtrip            python3 "$here/a_roundtrip.py" --seeds "$SEEDS" --tmp "$T"
expect 0 a-roundtrip-strict     python3 "$here/a_roundtrip.py" --seeds 5 --strict --tmp "$T"
for b in sort-unknown keep-resume-false keep-num-text drop-unknown; do
  expect 1 "a-break-$b"         python3 "$here/a_roundtrip.py" --seeds 30 --break "$b" --tmp "$T"
done
expect 1 a-mutant-sut           env FIGRAM_REAL="$FIGRAM" FIGRAM="$here/mutant_figram.sh" python3 "$here/a_roundtrip.py" --seeds 30 --tmp "$T"
expect 0 b-rfc                  python3 "$here/b_rfc_table.py" --random 4000
expect 0 b-rfc-strict           python3 "$here/b_rfc_table.py" --random 4000 --strict
expect 0 b-rfc-strict-fc01      python3 "$here/b_rfc_table.py" --random 4000 --strict fc01
for b in dup-last-wins no-surrogate float; do
  expect 1 "b-break-$b"         python3 "$here/b_rfc_table.py" --random 1000 --break "$b"
done
expect 0 c-schema               python3 "$here/c_schema_table.py" --tmp "$T"
for b in no-minlength const2 no-integer; do
  expect 1 "c-break-$b"         python3 "$here/c_schema_table.py" --break "$b" --tmp "$T"
done
expect 0 d-engine               python3 "$here/d_engine_requests.py"
for b in flip lax-id; do
  expect 1 "d-break-$b"         python3 "$here/d_engine_requests.py" --break "$b"
done
expect 0 e-cli                  python3 "$here/e_cli_names.py" --tmp "$T"
expect 0 e-cli-strict           python3 "$here/e_cli_names.py" --strict --tmp "$T"
expect 0 e-cli-control          python3 "$here/e_cli_names.py" --control --tmp "$T"
expect 0 f-parser-scaling       python3 "$here/f_parser_scaling.py" --figram "$FIGRAM" \
  --engine "$FIGRAM_ENGINE" --check --tmp "$T"
# FC-01 at the Lean level: every number text `parse` accepts is printed back into parseable text
if command -v lake >/dev/null; then
  expect 0 fc01-lean-check      lake env lean "$here/fc01_lean_check.lean"
else
  echo "fc01-lean-check: skipped (lake is not installed)"
fi
if [ -n "${FIGRAM_OLD:-}" ]; then
  expect 0 f-parser-diff        python3 "$here/f_parser_diff.py" "$FIGRAM_OLD" "$FIGRAM" --n 60000
fi
echo "unexpected exit codes: $fails"
[ "$fails" = 0 ]

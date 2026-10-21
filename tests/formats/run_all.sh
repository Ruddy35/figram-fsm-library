#!/usr/bin/env bash
# Runs every formats-correctness test, its strict mode and its deliberately
# broken oracles, logging each run with /home/user/figram-audit/run.sh.
#   tests/formats/run_all.sh [LOGDIR] [SEEDS]
# Expected: normal runs exit 0; every --break / mutant run exits 1.  The --strict
# runs count rows of audit findings as failures: FC-01 (P-12), FC-06 (P-13) and
# FC-03 (invalid UTF-8, P-18) are fixed, so they exit 0 now (they exited 1 on the
# audit baseline).  f-parser-scaling (P-10) checks time/RSS budgets; f-parser-diff
# runs only when FIGRAM_OLD names a baseline `figram` to compare with.
set -u
here="$(cd "$(dirname "$0")" && pwd)"
L="${1:-/home/user/figram-audit/agents/formats-correctness/LOGS}"
SEEDS="${2:-250}"
R=/home/user/figram-audit/run.sh
T="${TMPDIR:-/tmp}"
N="nice -n 10 timeout 900"
mkdir -p "$L"
expect() {  # expect RC NAME CMD...
  local want=$1 name=$2; shift 2
  $R "$L/$name" $N "$@" >/dev/null; local rc=$?
  local verdict=PASS; [ "$rc" = "$want" ] || verdict=UNEXPECTED
  printf '%-34s want=%s got=%s %s  %s\n' "$name" "$want" "$rc" "$verdict" "$(tail -1 "$L/$name.stdout")"
}
cd "$here/../.."
expect 0 a-roundtrip            python3 "$here/a_roundtrip.py" --seeds "$SEEDS" --tmp "$T"
expect 0 a-roundtrip-strict     python3 "$here/a_roundtrip.py" --seeds 5 --strict --tmp "$T"
for b in sort-unknown keep-resume-false keep-num-text drop-unknown; do
  expect 1 "a-break-$b"         python3 "$here/a_roundtrip.py" --seeds 30 --break "$b" --tmp "$T"
done
expect 1 a-mutant-sut           env FIGRAM="$here/mutant_figram.sh" python3 "$here/a_roundtrip.py" --seeds 30 --tmp "$T"
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
expect 0 f-parser-scaling       python3 "$here/f_parser_scaling.py" --figram "${FIGRAM:-bin/figram}" \
  --engine "${FIGRAM_ENGINE:-bin/figram-engine}" --check --tmp "$T"
if [ -n "${FIGRAM_OLD:-}" ]; then
  expect 0 f-parser-diff        python3 "$here/f_parser_diff.py" "$FIGRAM_OLD" "${FIGRAM:-bin/figram}" --n 60000
fi

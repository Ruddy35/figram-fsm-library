#!/usr/bin/env bash
# FC-01 / FC-06 regression set on a worktree's OWN stage1 build (.lake/build/bin).
#   fc_regression.sh WORKTREE LABEL [BINDIR]
# Tests are taken from WORKTREE (tests/formats, ffi/python-example, examples); binaries and the Lean
# environment for fc01_lean_check.lean from BINDIR's worktree (default WORKTREE).
# Each command is logged with run.sh into agents/integration/LOGS/fc-LABEL-*.log.
set -u
W="$1"; LABEL="$2"; B="${3:-$1}"
L=/home/user/figram-audit/agents/integration/LOGS
R=/home/user/figram-audit/run.sh
N="nice -n 10 timeout 900"
source /home/user/figram-audit/env.sh
cd "$W" || exit 2
export FIGRAM="$B/.lake/build/bin/figram" FIGRAM_ENGINE="$B/.lake/build/bin/figram-engine"
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
sha256sum "$FIGRAM" "$FIGRAM_ENGINE" > "$L/fc-$LABEL-binaries.sha256"
one() { local name=$1; shift; $R "$L/fc-$LABEL-$name" $N "$@" >/dev/null; local rc=$?
        printf '%-28s rc=%s  %s\n' "$name" "$rc" "$(tail -1 "$L/fc-$LABEL-$name.stdout")"; }
# regression tests (red on baseline per formats-correctness)
one b-rfc-strict-fc01  python3 tests/formats/b_rfc_table.py --random 4000 --strict fc01
one a-roundtrip-strict python3 tests/formats/a_roundtrip.py --seeds 5 --strict --tmp "$T"
one fc01-lean-check    bash -c "cd '$B' && lake env lean '$W/tests/formats/fc01_lean_check.lean'"
one json-fuzz-30000    python3 ffi/python-example/json_fuzz.py "$FIGRAM" 30000
one e-cli-strict       python3 tests/formats/e_cli_names.py --strict --tmp "$T"
# non-regression (must stay 0 before and after)
one a-roundtrip        python3 tests/formats/a_roundtrip.py --seeds 250 --tmp "$T"
one b-rfc              python3 tests/formats/b_rfc_table.py --random 4000
one c-schema           python3 tests/formats/c_schema_table.py --tmp "$T"
one d-engine           python3 tests/formats/d_engine_requests.py
one e-cli              python3 tests/formats/e_cli_names.py --tmp "$T"
one e-cli-control      python3 tests/formats/e_cli_names.py --control --tmp "$T"
one b-rfc-strict-fc03  python3 tests/formats/b_rfc_table.py --random 4000 --strict fc03
mkdir -p "$T/g"
one golden             bash -c "'$FIGRAM' golden '$T/g' >/dev/null && cmp '$T/g/golden.jsonl' examples/golden.jsonl && echo golden-identical"

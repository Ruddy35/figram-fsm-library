#!/usr/bin/env bash
# ffi-go regression set in a worktree: build (ffi/audit/build_audit.sh), regress.sh, init_race.sh 300,
# Go audit tests under -race, and (optionally) the Go block of verify.sh.
#   ffi_regression.sh WORKTREE LABEL [goblock]
set -u
W=$1; LABEL=$2; GOBLOCK=${3:-}
L=/home/user/figram-audit/agents/integration/LOGS
R=/home/user/figram-audit/run.sh
source /home/user/figram-audit/env.sh
cd "$W" || exit 2
one() { local name=$1; shift; $R "$L/ffi-$LABEL-$name" "$@" >/dev/null; local rc=$?
        printf '%-26s rc=%s  %s\n' "$name" "$rc" "$(tail -1 "$L/ffi-$LABEL-$name.stdout" | cut -c1-200)"; }
one build_audit nice -n 10 timeout 900 bash ffi/audit/build_audit.sh
[ -e ffi/out ] || ln -s audit/out ffi/out
one regress nice -n 10 timeout 900 bash ffi/audit/regress.sh
one init_race_300 nice -n 10 timeout 900 bash ffi/audit/init_race.sh ffi/audit/out/contract 300
cd ffi/go-example
one go_test_race nice -n 10 timeout 900 env CC=gcc CGO_LDFLAGS="${EXTRA_CGO:-} -L/nonexistent-$LABEL-$RANDOM" FIGRAM_INIT_RACE=50 \
    FIGRAM_CLI="$W/.lake/build/bin/figram" go test -race -count=1 -v \
    -run 'TestAudit(InitRace|RequestBadOp|LoadNUL|HelloAlwaysError|CloseRequestStorm|RequestNUL|ThreadCount|SIGPIPE)' .
grep -E "^(--- |ok|FAIL)" "$L/ffi-$LABEL-go_test_race.stdout" | head -20
if [ -n "$GOBLOCK" ]; then
  cd "$W"; T=$(mktemp -d)
  .lake/build/bin/figram trace examples/pizza.bot.json 200 40 7 > "$T/big.jsonl"
  .lake/build/bin/figram trace examples/pizza.bot.json 20 40 7 > "$T/small.jsonl"
  one go_verify_block nice -n 10 timeout 900 env CGO_LDFLAGS="${EXTRA_CGO:-}" bash ffi/audit/go_verify_block.sh "$T/big.jsonl" "$T/small.jsonl"
  rm -rf "$T"
fi

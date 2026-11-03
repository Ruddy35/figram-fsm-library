#!/usr/bin/env bash
# go_suite.sh NAME: with DEFECTS/NAME.full.patch applied (NAME=base: none), build libfigram.a and the Go
# driver, run the Go steps of verify.sh (golden scenarios of both documents, the pizza traces, the
# protocol-3 go tests), then restore the sources.
set -u
NAME=$1
R=/home/user/figram-audit/agents5/redteam
W=/home/user/figram-audit-worktrees/a5-redteam
RUN=/home/user/figram-audit/run.sh
L=$R/LOGS/$NAME; mkdir -p $L
cd $W
source /home/user/figram-audit/env.sh
export LEAN_CC=/home/user/figram-audit/fixes/memory/cc-quiet.sh
[ -z "$(git status --porcelain --untracked-files=no)" ] || { echo "tree not clean"; exit 2; }
[ "$NAME" = base ] || git apply "$R/DEFECTS/$NAME.full.patch" || exit 2
$RUN $L/go-ffi nice -n 10 timeout 1500 bash ffi/build.sh >/dev/null
rm -f clients/go/gofig
$RUN $L/go-build bash -c "cd clients/go && CC=gcc nice -n 10 go build -ldflags=-extldflags=-Wl,--gc-sections -o gofig ." >/dev/null
G=clients/go/gofig
for d in pizza features; do
  $RUN $L/go-golden-$d nice -n 10 timeout 600 $G -rounds 3 -bots 2 examples/$d.bot.json examples/golden.jsonl >/dev/null
  printf '%-22s exit=%-3s %s\n' go-golden-$d "$(grep -m1 '^exit_code:' $L/go-golden-$d.log | awk '{print $2}')" "$(grep -m1 '^bots' $L/go-golden-$d.stdout)"
done
$RUN $L/go-conf nice -n 10 timeout 600 $G -rounds 3 -bots 4 examples/pizza.bot.json $R/ref/pizza.jsonl >/dev/null
printf '%-22s exit=%-3s %s\n' go-conf "$(grep -m1 '^exit_code:' $L/go-conf.log | awk '{print $2}')" "$(grep -m1 '^bots' $L/go-conf.stdout)"
$RUN $L/go-test bash -c "cd clients/go && CC=gcc nice -n 10 timeout 600 go test -count=1 -v -run '^(TestFixCheckReplyProtocolChanges|TestFixStartWithEvent|TestFixOutcomes|TestAuditRequestBadOp)\$' ." >/dev/null
printf '%-22s exit=%-3s %s\n' go-test "$(grep -m1 '^exit_code:' $L/go-test.log | awk '{print $2}')" "$(grep -E '^--- (PASS|FAIL)' $L/go-test.stdout | tr '\n' ' ')"
git checkout -- Figram Main.lean EngineMain.lean
echo "restored: $(git status --porcelain --untracked-files=no | wc -l) modified"

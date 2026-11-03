#!/usr/bin/env bash
# go_stale.sh: does `go build -o gofig` (as verify.sh runs it) relink against a changed ffi/out/libfigram.a?
# 1. base libfigram.a + gofig built as verify.sh does; 2. apply defect d11a (the engine no longer writes
# `detached`), ffi/build.sh, the same go build; compare gofig before/after and run the golden check;
# 3. rebuild with -a and run the golden check again; restore the sources and rebuild base.
set -u
R=/home/user/figram-audit/agents5/redteam
W=/home/user/figram-audit-worktrees/a5-redteam
cd $W; source /home/user/figram-audit/env.sh; export LEAN_CC=/home/user/figram-audit/fixes/memory/cc-quiet.sh
G=clients/go
gob() { (cd $G && CC=gcc go build -ldflags=-extldflags=-Wl,--gc-sections -o gofig . 2>&1); }
echo "--- 1. base"
bash ffi/build.sh >/dev/null 2>&1 || exit 2
gob; sha_lib0=$(sha256sum ffi/out/libfigram.a | cut -c1-16); sha0=$(sha256sum $G/gofig | cut -c1-16)
echo "libfigram.a $sha_lib0 gofig $sha0"
$G/gofig -rounds 1 -bots 1 examples/features.bot.json examples/golden.jsonl; echo "golden check exit $?"
echo "--- 2. defect d11a applied, ffi/build.sh, the go build of verify.sh"
git apply $R/DEFECTS/d11a-no-detached-flag.full.patch || exit 2
bash ffi/build.sh >/dev/null 2>&1 || { git checkout -- Figram; exit 2; }
gob; sha_lib1=$(sha256sum ffi/out/libfigram.a | cut -c1-16); sha1=$(sha256sum $G/gofig | cut -c1-16)
echo "libfigram.a $sha_lib1 gofig $sha1 (gofig relinked: $([ $sha0 = $sha1 ] && echo NO || echo yes))"
$G/gofig -rounds 1 -bots 1 examples/features.bot.json examples/golden.jsonl; echo "golden check exit $?"
echo "--- 3. go build -a"
(cd $G && CC=gcc go build -a -ldflags=-extldflags=-Wl,--gc-sections -o gofig . 2>&1)
sha2=$(sha256sum $G/gofig | cut -c1-16); echo "gofig $sha2"
$G/gofig -rounds 1 -bots 1 examples/features.bot.json examples/golden.jsonl; echo "golden check exit $?"
git checkout -- Figram Main.lean EngineMain.lean
bash ffi/build.sh >/dev/null 2>&1; (cd $G && CC=gcc go build -a -ldflags=-extldflags=-Wl,--gc-sections -o gofig . 2>&1)
echo "restored: $(git status --porcelain --untracked-files=no | wc -l) modified; base gofig $(sha256sum $G/gofig | cut -c1-16)"

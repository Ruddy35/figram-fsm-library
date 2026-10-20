#!/usr/bin/env bash
# Gate: reference build of the baseline with the from-source Lean 4.35.0 (stage1).
# Every step is logged via run.sh (command, exit code, stdout/stderr) to /home/user/figram-audit/LOGS/3x-*.
set -u
A=/home/user/figram-audit; L=$A/LOGS; R=$A/run.sh
S1=/home/user/leanprover/lean4/build/release/stage1
printf 'export PATH=%s/bin:$PATH\n' "$S1" > $A/env.sh
source $A/env.sh
$R $L/30-lean-version lean --version
$R $L/30-lake-version lake --version
$R $L/30-lean-githash bash -c 'lean --githash; git -C /home/user/leanprover/lean4 rev-parse HEAD'
W=/home/user/figram-audit-worktrees/ref
[ -d $W ] || git -C /home/user/figram-fsm-library worktree add -b audit/ref $W audit/baseline
cd $W
$R $L/31-lake-build lake build
echo "warnings: $(grep -ci warning $L/31-lake-build.stdout)" | tee $L/31-lake-build.warnings
$R $L/32-lake-build-static lake build Figram FigramApi:static
mkdir -p $A/fresh
cp .lake/build/bin/figram .lake/build/bin/figram-engine $A/fresh/
cp $L/31-lake-build.log $A/fresh/build.log
# reproducibility vs bin/ (expected to differ: gcc/libstdc++ vs official clang/libc++ toolchain)
T=$(mktemp -d); cp .lake/build/bin/figram .lake/build/bin/figram-engine $T/ && strip $T/figram $T/figram-engine
$R $L/33-hash-vs-bin bash -c "cd $T && sha256sum figram figram-engine | diff - $W/bin/SHA256SUMS"
$R $L/33-ldd-fresh ldd $A/fresh/figram-engine
# original ffi/build.sh (expected to fail on a from-source toolchain: no bundled libc++/libuv/libgmp .a)
$R $L/34-ffi-build-original bash ffi/build.sh
echo gate-done

#!/usr/bin/env bash
# Orchestrator gate on the pushed tree (local from-source toolchain): build, strip, sums, full verify.
set -u
source /home/user/figram-audit/env.sh
W=/home/user/figram-audit-worktrees/release-check; L=/home/user/figram-audit/fixes/release/LOGS; T=/home/user/figram-audit/fixes/release/sums
cd "$W" || exit 1
/home/user/figram-audit/run.sh $L/10-lake-build nice -n 10 lake build || exit 1
rm -rf "$T"; mkdir -p "$T"; cp .lake/build/bin/figram .lake/build/bin/figram-engine "$T"/; strip "$T"/figram "$T"/figram-engine
(cd "$T" && sha256sum figram figram-engine > SUMS)
FIGRAM_SHA256SUMS=$T/SUMS /home/user/figram-audit/run.sh $L/11-verify-full nice -n 10 timeout 2400 ./verify.sh
echo "verify exit: $?"
git status --short | head

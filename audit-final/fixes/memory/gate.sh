#!/usr/bin/env bash
# Полный шлюз на fix/memory (тулчейн из исходников; LEAN_CC убирает его лишние .so при компиляции C).
set -u
source /home/user/figram-audit/env.sh
export LEAN_CC=/home/user/figram-audit/fixes/memory/cc-quiet.sh
W=/home/user/figram-audit-worktrees/fix-memory; L=/home/user/figram-audit/fixes/memory/LOGS; T=/home/user/figram-audit/fixes/memory/sums
cd "$W" || exit 1
/home/user/figram-audit/run.sh $L/40-lake-build nice -n 10 lake build || exit 1
rm -rf "$T"; mkdir -p "$T"; cp .lake/build/bin/figram .lake/build/bin/figram-engine "$T"/; strip "$T"/figram "$T"/figram-engine
(cd "$T" && sha256sum figram figram-engine > SUMS)
FIGRAM_SHA256SUMS=$T/SUMS /home/user/figram-audit/run.sh $L/41-verify-full nice -n 10 timeout 2400 ./verify.sh
echo "verify exit: $?"
/home/user/figram-audit/run.sh $L/42-check_docs python3 -I audit/docs/check_docs.py . .lake/build/bin/figram .lake/build/bin/figram-engine
/home/user/figram-audit/run.sh $L/43-ffi-doc_check bash ffi/audit/doc_check.sh
git status --short | head

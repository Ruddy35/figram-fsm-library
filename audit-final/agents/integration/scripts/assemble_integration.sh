#!/usr/bin/env bash
# Assemble audit/integration = baseline + verified changes (tests before fixes), keeping authorship.
# Each cherry-pick gets "(cherry picked from commit X)" + optional integration note BEFORE the trailers.
# Usage: assemble_integration.sh WORKTREE   (worktree must be on audit/integration at baseline d69bbaf)
set -euo pipefail
W=$1; cd "$W"
S=/home/user/figram-audit/agents/integration/scripts
SCR=/tmp/claude-0/-home-user-figram-fsm-library/57129652-0dc1-5f90-93b6-ee8cd24dbb93/scratchpad
mkdir -p "$SCR"
[ "$(git rev-parse HEAD)" = d69bbaf7881a0ae1c11b75a35cf5193ad604b925 ] || { echo "not at baseline"; exit 1; }
TRAILERS='Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01JgWRtX2jyUidN8gxEhe5qx'

msg_for() {  # msg_for SHA NOTE -> message file
  local sha=$1 note=${2:-} f="$SCR/msg.$1"
  { git log -1 --format=%B "$sha" | sed '/^Co-Authored-By:/,$d' | sed -e :a -e '/^\n*$/{$d;N;ba' -e '}'
    [ -n "$note" ] && printf '\nIntegration note: %s\n' "$note"
    printf '\n(cherry picked from commit %s)\n\n%s\n' "$(git rev-parse "$sha")" "$TRAILERS"; } > "$f"
  echo "$f"
}
cp1() {  # cp1 SHA [NOTE]
  git cherry-pick --no-commit "$1" >/dev/null
  git commit -q -C "$1" >/dev/null           # keeps author + author date
  git commit -q --amend --no-edit -F "$(msg_for "$1" "${2:-}")"
  echo "$(git log -1 --format='%h %an %s')"
}

# A. formats-correctness tests (red on baseline: FC-01, FC-06)
cp1 897bfc8; cp1 177355a
# B. proofs audit tooling, strict gate, counterexamples, glue lemmas (TextRoundTrip red on baseline)
cp1 e0091ba; cp1 44f8b39; cp1 b5bc7e9
# C. FC-01 + FC-06 fix (chosen over the equivalent FC-01 part of d116a64)
cp1 4a38b91
# D. d116a64: its FormatIO/FORMAT change is identical in effect to 4a38b91 -> keep only its test adjustment
if ! git cherry-pick --no-commit d116a64 >/dev/null 2>&1; then
  git checkout --ours Figram/FormatIO.lean FORMAT.md; git add Figram/FormatIO.lean FORMAT.md
fi
git diff --cached --stat
git commit -q -C d116a64
f=$(msg_for d116a64 "FC-01 is fixed by 4a38b91 (formats-correctness), whose render
condition is the same predicate (-maxExponent <= e) and gives byte-identical output; this
commit keeps only the audit/proofs/Counterexamples.lean adjustment, the FormatIO.lean and
FORMAT.md hunks were dropped as duplicates.")
{ echo "test(proofs): Counterexamples.lean after the FC-01 fix (reduced d116a64; the fix itself is 4a38b91)"; echo
  echo "Original subject: $(git log -1 --format=%s d116a64)"; tail -n +2 "$f"; } > "$f.2"
git commit -q --amend --no-edit -F "$f.2"
echo "$(git log -1 --format='%h %an %s')"
# E. proofs: strict gate in verify.sh (TEST) + doc wording (DOC)
cp1 90201dd; cp1 d9934d5
# F. python-client: tests, then fix
cp1 1df3ef8; cp1 2550540; cp1 bbf7ac7; cp1 d8f658b; cp1 51c31ce
# G. engine-timers: protocol suite + HOST contract test, then docs
cp1 c9b7a1a; cp1 c6eecd0; cp1 8edc9a4; cp1 998a539
# H. host-perf: host_sim + HOST.md (drop the hunk for its audit harness, not integrated)
git cherry-pick --no-commit c8bb903 >/dev/null 2>&1 || true
git rm -q --cached audit/host-perf/host_sim_mutants.py 2>/dev/null || true; rm -f audit/host-perf/host_sim_mutants.py
rmdir audit/host-perf 2>/dev/null || true
git status --short | grep -v '^M ' && { echo "unexpected state after c8bb903"; exit 1; } || true
git commit -q -C c8bb903
git commit -q --amend --no-edit -F "$(msg_for c8bb903 "the hunk for audit/host-perf/host_sim_mutants.py (host-perf audit
harness, not part of audit/integration) is dropped; HOST.md merges with 998a539 (engine-timers)
without textual conflicts, rules of both kept (reviewed by hand).")"
echo "$(git log -1 --format='%h %an %s')"
cp1 1dfe574
# I. core-compiler suite + integration regression tests for CC-01 (red on baseline)
cp1 7918908; cp1 3d73782
# I (cont.). integration regression tests for CC-01 (my commits from scratch branches, red on baseline)
git cherry-pick 8ec1006 >/dev/null; echo "$(git log -1 --format='%h %an %s')"   # ptsuite U4h/U5h
git cherry-pick a291e1f >/dev/null; echo "$(git log -1 --format='%h %an %s')"   # core-compiler CC01=fixed
# K. ffi-go: tests (+ my fix of a data race in one test), then fixes
cp1 96ca9aa
git cherry-pick acf6774 >/dev/null; echo "$(git log -1 --format='%h %an %s')"
cp1 4998244; cp1 a027905
# J. CC-01 fix: code + proof repair (scratch 69a634e) + docs for the fixed behaviour
git cherry-pick --no-commit 69a634e >/dev/null
python3 "$S/apply_cc_docs.py" "$W"
git add -A README.md FORMAT.md HOST.md Figram
{ git log -1 --format=%B 69a634e | sed '/^Co-Authored-By:/,$d' | sed -e :a -e '/^\n*$/{$d;N;ba' -e '}'
  printf '\nDocs: README decisions 2 and 7, FORMAT.md section 7 rule 2 and the HOST.md section 6\ntimers bullet (998a539 described the unfixed engine) now state the fixed behaviour.\n\n%s\n' "$TRAILERS"; } > "$SCR/msg.cc"
git commit -q -F "$SCR/msg.cc"; echo "$(git log -1 --format='%h %an %s')"
git log --oneline d69bbaf..HEAD | wc -l

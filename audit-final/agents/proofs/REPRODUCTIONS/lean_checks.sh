#!/usr/bin/env bash
# Runs the audit's Lean checks on a worktree (default: proofs worktree): strict gate,
# counterexamples (+negative control), pinned statements (+negative control), glue lemmas.
set -u
WT=${1:-/home/user/figram-audit-worktrees/proofs}
source /home/user/figram-audit/env.sh
cd "$WT"; [ -d .lake ] || cp -a /home/user/figram-audit/fresh/lake-cache .lake
lake build >/dev/null || exit 1
rc=0
bash audit/proofs/strict_gate.sh | tail -1 || rc=1
lake env lean audit/proofs/Counterexamples.lean >/dev/null && echo "counterexamples: ok" || rc=1
lake env lean audit/proofs/CounterexamplesNegative.lean >/dev/null 2>&1 && { echo "negative control did not fail"; rc=1; } || echo "counterexamples negative control: red as expected"
lake env lean audit/proofs/Statements.lean >/dev/null && echo "statements: ok" || rc=1
lake env lean audit/proofs/StatementsNegative.lean >/dev/null 2>&1 && { echo "negative control did not fail"; rc=1; } || echo "statements negative control: red as expected"
lake build FigramAudit >/dev/null && lake env lean audit/proofs/GlueAxioms.lean | grep -v -E 'propext|Quot.sound|Classical.choice' | grep . && rc=1
lake env leanchecker FigramAudit && echo "glue: built, standard axioms, leanchecker ok" || rc=1
exit $rc

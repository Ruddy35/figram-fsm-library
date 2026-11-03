# Auditor `formal` (final audit): formalization coverage

Worktree `/home/user/figram-audit-worktrees/a5-formal` at 7239ab7. Brief: `tools/BRIEF5.md`. The
harness did not let the agent write REPORT.md; this is its final message, recorded verbatim by the
orchestrator. Its scratch Lean files and logs are next to this file (`Gaps.lean`, `Inst.lean`,
`SharedDeps.lean`, `*.sh`, `LOGS/`). The orchestrator's handling of each finding is in
`../../FIXES.md`.

---

I couldn't write `REPORT.md`: the harness blocks report files from subagents, so the findings are below. Commands, logs and scratch files are in `/home/user/figram-audit/agents5/formal/` (`Inst.lean`, `Gaps.lean`, `SharedDeps.lean`, `*.sh`, `LOGS/`). Worktree `git status` is clean, base `7239ab7`.

**Answer:** the formalization is honest (no P0), but not complete. Two P1 gaps remain: the new V5/V6 checks are written as `partial` functions that no theorem can reason about, and several claimed properties had no theorem — I proved those in scratch.

**Gates (item 5), all run in the worktree:**
- `theorems.lean` exit 0: 70 statements, standard axioms only.
- `diff THEOREMS.expected` exit 0.
- `strict_gate.sh` exit 0 (299 theorems, leanchecker passes).
- `Statements.lean` exit 0; the negative controls fail with 2 and 4 errors as required; `check_docs --no-run` reports 0 problems.
- `Statements.lean` restates all 24 new theorems. It skips 9 pinned ones, including the changed `startWith_unmatched` (P3).

**P1-1 — V5/V6 cannot be connected to `check`.**
- `Doc.check` computes V5 (outcome events) and V6 (at most once in `do`) over `allEvents` and `allVariants` (Format.lean:448, :453). Both are `partial`, so Lean cannot unfold them; `allVariants` is new in 0.3.0. The strict gate lists them as opaque.
- So "`check = []` implies every `call:` arrow names a declared outcome" and "a step waits for at most one outcome per action" (format.md:126-129) can never be proved as written. proofs.md:242 only admits the "texts of check diagnostics" are uncovered.
- Proved the semantic half (Gaps G7): under V6 stated on the chart, a step calls each action with outcomes at most once, for the chart and the compiled machine.
- Fix: write these helpers by structural recursion, like `flatNode`, then prove `check = [] → V6`.

**P1-2 — claimed properties without a theorem; all proved in `Gaps.lean` (exit 0, standard axioms):**
- G10: each call is answered once for good. The pinned `step_outcome_once` only covers the delivery right after the outcome.
- G8: every action of a document's variant is a call. This discharges `stay_keeps`' unproved hypothesis, so decision 13 holds end to end for documents.
- G2: under any document version, a wait still accepts its outcome.
- G9: `restart` leaves no waits on a compiled machine (`restart_pending` is weaker).
- G1: the engine never writes the same wait twice.
- G3: a timer consumed by a variant without a target ignores its redelivery.
- G4: on the chart, an accepted outcome with no matching variant (no arrow, or every condition false) only loses its wait.
- G5: a decoded named event never has an outcome or timer key.
- G6: calls of consecutive steps carry tokens t and t+1.
- Recommend moving these into `OutcomeProofs.lean` and pinning them.

**P2:**
1. host-contract.md:340-342 says an old timer's entry "does not survive the first transition". That is false since 0.3.0: a variant without a target keeps it (`stale_timer_stay.sh`, exit 0).
2. An outcome name the action never declared, sent with a valid token, is accepted and silently removes the wait. A host typo leaves the user waiting forever (`undeclared_outcome.sh`). The tests assert this (protocol_edges.py:289) but no doc mentions it.
3. `Chart.step` uses the engine's own helpers for waits and stays (`accepts`, `settle`, `consume`, `addWait`, `stay`, `realize`; `SharedDeps.lean`). Neither `compile_correct` nor the conformance suite can catch an error there, contrary to testing.md:57-58. I read the helpers against decisions 11–13: they match.
4. Some proofs.md rows say more than the theorem: `step_outcome_once` (next step only), `startWith_unmatched` (does not cover an outcome that has an arrow at the initial message — shown by `decide`), `restart_pending`, `startWith_fresh` (no-event case), `decEvent_outcome` (token written as `tok·10^0` only), `step_undetach` (whole machine, not one action).
5. Hypothesis, by reasoning, not run: the outcome `update_id` `call:<action>:<token>` is not safe after a database rollback; timers get a run id, outcomes do not.

**P3:**
- The snapshot decoder accepts a wait whose token exceeds `seq`, which breaks "answered once" and token uniqueness (`nonfresh_snapshot.sh`).
- §8 rule 1 says recovery is "as for start", but recovery accepts outcomes and `start` does not.
- `Chart` allows a "back" variant without a target, which acts as a self-loop; no document can produce one.
- `startWith_unmatched` is not restated in `Statements.lean`.

**Item 1 — claims table.** Every claim in decisions 11–13, §8 and the host contract maps to a pinned theorem (directly, or a Machine theorem plus `compile_correct`), the definition of `Chart.step`, or a G-proof. Two are covered by tests only: V5 and "`failed` exists for every action with outcomes" (the `#guard`s in Examples.lean:413-429). Two are covered by encoder definition plus protocol_edges tests, with no theorem: `token` and `detached` on `call`. One is false (P2-1).

**Item 2.** `Inst.lean` (exit 0) discharges by `decide`, on the compiled `pizza`/`features` machines, the hypotheses of every new or changed theorem that has any. Examples: `startWith_eq_step` where the initial message has a timer; `enterBot_eq_realize_some` and `_none` in recovery with an outcome; `step_stay` on a timer firing; `step_outcome_once` with the machine's `wf`. None is vacuous. The weaker ones are listed in P1-2 and P2-4.

**Item 3.** `compile_correct` and `startWith_correct` quantify over every event, including outcomes, and the "no target" (stay) branch is inside `Chart.step`. So the engine cannot diverge from the chart there; the only caveat is P2-3.

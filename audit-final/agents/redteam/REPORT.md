# Auditor `redteam` (final audit): would the tests catch plausible bugs?

Worktree `/home/user/figram-audit-worktrees/a5-redteam` at 6d8d4c1. Brief: `tools/BRIEF5.md`. The
harness did not let the agent write REPORT.md; this is its final message, recorded verbatim by the
orchestrator. Its defects (patches), scripts, reproductions and logs are next to this file
(`DEFECTS/`, `scripts/`, `repro/`, `ref/`, `LOGS/`, `results_raw.txt`; the compiled base and
mutant binaries of `bin/` are excluded and rebuild from `DEFECTS/*.full.patch`, see
`../../MANIFEST.json`). The orchestrator's handling of each finding is in `../../FIXES.md`.

---

REDTEAM: 49 injected defects (46 engine/Compile/Format/Warnings, 3 Python client), one at a time in the a5-redteam worktree. Each mutated engine ran against reference scenarios from the ORIGINAL figram. Code shared with the oracle (Core helpers, Engine encoders, Format) was also run with the mutated figram, which is the real gate configuration. Proofs that broke were stubbed with sorry and listed in LOGS/<d>.stubbed. Tree clean at the end; the rebuilt base binaries are byte-identical to the saved originals (LOGS/final-rebuild.log). **REPORT.md was not written**: the harness blocks report files for subagents, so this message is the report. Raw matrix: agents5/redteam/results_raw.txt. Runs: LOGS/<defect>/<suite>.log. Patches: DEFECTS/<d>.patch and .full.patch.

**Defect → first verify.sh catcher (failing/total); CI-only catchers**
- 1 outcome without an arrow keeps its wait: conf-golden 2/170, edges 5/250. 1b (all conditions false): only random_docs, 59/100 docs; golden, traces, edges, host and Go stay green.
- 2a/2b token check (any wait of the action / any token): conf-golden 2 and 3, edges 4 and 5. 3 latest acts as every, 4 every acts as latest: conf-golden 1, edges 1.
- 5 a stay variant re-enters: conf-golden 13. 6/6b stay timer not consumed: conf-golden 1. 7 entering the bot, stay leads to the snapshot's message: conf-golden 1, edges 3, Go golden 3/306. 29 similar: conf-golden 1.
- 8a/8b recovery drops the waits / keeps the accepted wait: conf-golden 2 and 1. 9 restart keeps pending: edges 1/250, d-engine, Go TestFixOutcomes. 10 start accepts `call:`: edges 3, client_contract.
- 11a/11b encoder drops `detached`/`token`: conf-golden 11, host, Go. 12a/12b call token is `seq`: conf-golden 11.
- 13a/b/d decoder reverses or sorts `pending`: conf-traces 494, 494 and 59 of 17602, snapshots 1/49; golden, edges and Go stay green. 13c encoder writes pending oldest first: conf-golden 1.
- 14a/14b toChart ignores every/detached: conf-golden 1 and 11.
- 15a/15b/15d (check drops V6 / accepts an undeclared outcome / accepts an outcome of a plain action): no verify.sh test catches them; only the build #guard at Examples.lean:423, and in CI check_diff and formats c-schema. 15c (check rejects `failed`): every suite.
- 16 section/root outcome arrows not copied, 17 a move clears waits, 18 settle removes all waits, 19 stay keeps seq: conf-golden 3, 2, 2, 13.
- 22–26, 30, 31, 33, 38, 48 and the client defects c01–c03: caught by edges, conf-golden, host, snapshots or client tests. 28 (`fmt` drops `detached`): golden comparison and formats a-roundtrip.
- **Survive ./verify.sh:**
  - 20 (stay timer firing clears ALL timers); CI: directed 2/162, diff 34.
  - 21 (`need` for a stale outcome in recovery); CI: diff only, 1 divergence in 100 docs, 22 in 600.
  - 27a/27b (outcome warning forgets `failed` / skips stay variants); CI: lint_warnings only.
- Equivalent on reachable snapshots: 39 (every without dedupe) and 41 (settle after the move). Both differ only for a forged wait with token seq+1. diff at gate size: 0/600 docs for 41, 1 for 39.
- With shared-code defects and the mutated oracle, regenerated traces and random_docs show 0 mismatches; only the committed golden files and hand-written suites catch them.

**Findings**
- **P1-1 (defect 20):** a stay timer firing that clears every timer passes the whole local gate.
  - After repairing two unpinned helper proofs (Inv.consume, consume_timers_mem) without sorry: lake build has 0 warnings, the 70 pinned statements are identical, and strict_gate.sh says PASS (LOGS/proofgate-d20r.log, LOGS/strict-gate-d20r.log).
  - Every verify.sh suite stays green with this engine; golden output and coverage are unchanged.
  - docs/proofs.md:91 says stay_keeps proves "keeps the other timers", but its statement (Statements.lean:188) is only `timers = (s.consume ev).timers`, which holds for the broken `consume` too.
  - User impact: repro/d20.py on repro/d20.bot.json. With the mutant, the timeout timer is ignored after the reminder fires, so the user never leaves (exit 1; the base exits 0).
  - Fix: state it as `= erase s.timers id`, pin it, and add a two-timer stay case to the example document.
- **P1-2:** the Go steps of verify.sh (lines 375–376) can test a stale engine. `go build -o gofig` does not relink when only ffi/out/libfigram.a changed (Go does not track cgo C libraries), and gofig persists because it is git-ignored.
  - LOGS/go-stale.log: with defect 11a and ffi rebuilt, gofig keeps the same hash and the golden check shows 0 mismatches; after `go build -a` it shows 9.
  - CI is not affected (fresh checkout, cache off); local and repeated gate runs are.
  - Fix: `rm -f gofig gofig-race` before building.
- **P2-1:** `need` minimality for a stale outcome in recovery (defect 21) is checked only by random diff (proofs.md:139 says it is not a theorem). Add a hand-written case.
- **P2-2:** the outcome warnings (defects 27a/27b) are tested only by tests/compiler/lint_warnings.py, which verify.sh does not run.
- **P2-3:** the example documents have no all-guarded outcome arrow, no two-timer message with a stay timer handler, and no guarded outcome arrow at the initial message. That is why 1b, 20 and 21 are invisible to golden, traces, edges, host, C and Go.
- **P2-4:** docs/testing.md:57 says the reference scenarios do not come from the engine, but their JSON comes from Engine.encSnapshot/encActions (Trace.lean:63, 68, 78). Encoder defects are invisible to the regenerated traces.
- **P3:** V5/V6 are guarded in verify.sh only by one #guard that compares message text. A broken proof is a weak signal by itself (defects 20 and 41 broke only structural or unpinned proofs).

**Floors:** they equal the current counts (mutants 211, self-test 72, snapshots 49, edges 250). Conformance and Go only require requests > 0, but the golden count is exact and coverage must be N of N. The only check that passes regardless of the code is the stale Go one (P1-2).

Paths: /home/user/figram-audit/agents5/redteam/{DEFECTS,LOGS,scripts,repro,results_raw.txt}. Binaries kept: bin/ (base) and bin/mut/ for d11a, d20, d21, d27a, d27b.

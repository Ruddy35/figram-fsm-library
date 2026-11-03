# Auditor `semantics` (final audit): the engine against the prose

Worktree `/home/user/figram-audit-worktrees/a5-semantics` at 7239ab7. Brief: `tools/BRIEF5.md`.
The harness did not let the agent write REPORT.md; this is its final message, recorded verbatim by
the orchestrator. Its cases, scripts and logs are next to this file (`REPRODUCTIONS/`, `scripts/`,
`LOGS/`). The orchestrator's handling of each finding is in `../../FIXES.md`.

---

**Verdict:** for the 0.3.0 features, the engine does exactly what the prose says (docs/format.md sections 3, 4, 7, 8; docs/host-contract.md). I found no P0 or P1. The findings are prose gaps and tooling nits. REPORT.md was not written because the harness blocks subagent report files, so the findings are below. Paths are relative to `/home/user/figram-audit/agents5/semantics/` (evidence in `LOGS/`, inputs in `REPRODUCTIONS/`). The worktree's `git status` is clean.

**How I checked** (expected replies derived from the prose and written before each run):
- **169 hand cases, 0 disagreements** (`REPRODUCTIONS/cases/{out,stay,bg,recneed,nested,reqedge}`, rerun with `scripts/case.sh`). They cover every item on your list, including recovery, `start`, `restart`, pending edge cases and request validation.
- **The cases can fail:** all 216 engine mutants of my three main documents are killed (`LOGS/mutkill_*.log`).
- **Random differential test, 50 000 requests, 0 mismatches:** `scripts/prose_ref.py` is my own interpreter of decisions 1–13 written from the prose. I ran it on 550 random documents and on the pizza, features and annotations examples (`LOGS/diff_*.log`).
- **Format layer:** 47 `figram check` verdicts all match (`LOGS/format_check.log`); 10 warning documents match except W7 below; the `fmt` round trip is canonical and idempotent, with identical engine replies before and after; decision 11 holds (dropping `detached` changes nothing else).
- **Doctests** (`ci/check_docs.py`) pass, except the FFI example, which needs `ffi/out` to be built first.

**P2-1. Reserving `call:` breaks 0.2.0 documents.**
- The prose still promises compatibility: format.md:61-62 (C5c, old documents mean the same) and :261-262 (a change of meaning needs `"figram": 2`). 0.2.0's own text said only `timer:` was reserved ("does not restrict other event names", `git show a9c60d1:docs/format.md` line 191-193).
- Reproduction: `REPRODUCTIONS/compat/v02_call_event.bot.json` uses an arrow on `call:crm:done` and no 0.3.0 feature. `figram check` exits 1 and the engine refuses to load it (`LOGS/compat_check.log`, `LOGS/compat_engine.log`).
- The only mention is CHANGELOG.md:57. Fix: state the exception and a migration step (rename such events) in format.md, or gate the reservation behind `requires`.

**P2-2. No host rule for an outcome event that arrives for a user without a snapshot.**
- host-contract.md:46-48 says to drop such a timer event, but says nothing about an outcome event. `start` rejects it with `bad_request` (cases `out/start-outcome*`), which host-contract.md:296 treats as a host bug that goes to the failure queue with an alert.
- Fix: extend :46-48 to outcome events (drop it and record `call:<action>:<token>` as processed).

**P3 nits:**
1. format.md:461 says duplicate waits are something "the engine never writes". It does write back duplicates it was given on a move (`out/dup-move`, `cases/out.actual.jsonl:34`). Should say "never creates".
2. format.md:497 is garbled: "`start` takes neither event and no `token`". Should say: neither a timer nor an outcome event, and no `token`.
3. format.md:495-498 never says that malformed reserved events (`call:x`, `call::ok`, `call:mk:`, `timer:`) are `bad_request` in `step`. The engine rejects them (`reqedge/*`), and CHANGELOG.md:57 only says such events "need token".
4. `"outcomes": []` is not listed in row 11 (format.md:82) or the rules at :123. It silently means a plain action, and a `call:x:failed` arrow then fails with the misleading error "`x` is not an action with the outcome `failed`" (`LOGS/F60.log`, `LOGS/F61.log`).
5. False-positive warning: a root `timer:t` arrow without `to` that calls an action with outcomes warns on message `g`, which never starts `t` (`warn/W7_root_timer_stay.bot.json`). Cause: Figram/Warnings.lean:36 takes every message under the arrow.
6. host-contract.md:347 says an outcome of a removed action, or of one that lost its outcomes, is handled by "the arrows of the new version". V5 (format.md:114) makes such an arrow impossible, so it is always ignored (`out/gone-action`).
7. clients/python/figram_client.py:805 says a non-accepted outcome returns "the unchanged snapshot without actions". In recovery it is a start over with actions and `recovered: true` (`LOGS/client_outcome_docstring.log`).
8. The grammar comments at format.md:175-176 and 194 cite "decision 11/12/13" without "of section 7"; section 3's rows 11–13 are different decisions.
9. Suggestion only (the engine follows the prose here): an undeclared outcome such as `call:mk:zzz` with a valid token removes the wait without any signal (`out/undeclared-outcome`). A host typo then strands the user on "please wait".

I corrected one expectation of my own (`nested/ok-at-top`); the engine was right there.

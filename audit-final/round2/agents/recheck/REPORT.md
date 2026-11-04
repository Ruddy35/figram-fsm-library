**recheck: the changes in 7239ab7..598fec6.** The harness blocked REPORT.md, so this message is the whole report. Everything is under `/home/user/figram-audit/agents6/recheck/`: logs in `LOGS/` (written with run.sh, so each has the command and exit code), scripts and documents in `repro/` and `diffcheck/`. My scratch builds were copies of 598fec6 with one deliberate change each; the diffs are in `repro/*.diff`. The worktree's `git status` is clean.

**P1-1. Mutation testing reports "211 of 211 detected", but 17 mutants are never run, and one of them survives when it is.**
- The cause is in `EngineMain.lean:80-90`. `specInRange` accepts a name only for `drop-call`; every other kind needs a number. So all `detach-flip:<action>`, `wait-free:<action>` and `wait-swap:<action>` mutants are refused with exit 2 before any request.
- `conformance.run` treats the non-zero exit as an error, and `mutation.py:41-43` counts that as a kill. This makes `testing.md:66-68` and FIXES IP-1 ("211/211") untrue.
- With `specInRange` fixed (3 lines), the result is 210 of 211: `wait-swap:create_order` survives on pizza (`L/mutation_fixedengine.log`).
- That mutant is live, not equivalent: a user who leaves `placing` and orders again ends up with one wait on the real engine and two waits on the mutant (`repro/wait_swap_live.py`, `L/wait_swap_live_*`). No verify.sh scenario has a second call while the first wait is still pending.
- `tests/compiler` has the same problem: 90 of 2659 mutants are refused and counted as killed. Once they actually run, the random driver does kill them, so nothing is hidden there. The classification added in e1425d1 is sound but currently never reached.
- 16 mutants of action names containing `:` stay refused even with the fix, because `apply` and `specInRange` split on every `:`.
- Fix: accept names for these kinds in `specInRange` and split at the first `:` only; treat a refused mutant as a harness error, not a kill; add a scenario that calls an action again while its wait is pending.

**P2-1. `format.md:403-405` still says `failed` means "the action could not be performed".** That is the reading PR-P1-1 found wrong. `host-contract.md:142-151` and `CHANGELOG.md:62` now say the opposite. Reword decision 12 the same way and point to reconciliation.

**P2-2. A weakened V6 passes every local gate.**
- I changed `waitNames` to count only `latest` actions (`repro/waitNames_weakened.diff`). Every local gate stayed green: `lake build`, the 99 pinned statements (identical), `Statements.lean`, `strict_gate`, and the full `./verify.sh` ("ALL CHECKS PASSED", `L/weak_verify.log`).
- Meanwhile `figram check` accepted `repro/docs/every_twice.bot.json`, and the engine emitted two `call ping` with token 4 but kept one wait, so one of the two calls can never be answered.
- Only CI's `check_diff.py` catches it (`L/weak_check_diff.log`, 5 disagreements).
- Fix: pin what `waitNames`/`WaitsOnce` mean in a theorem, or restate them inline in `Statements.lean` (as `AllCalls` already is), and add a local twice-in-`do` case for `every` and detached actions.

**P3**
1. `trace --directed` took 4 min 15 s on a 3-message document with an `every` action and an unreachable message (`repro/docs/every_growth.bot.json`). It does terminate. The cause is that `Trace.placeKey` counts each repeated wait as a new place; keying on the set of waiting actions would fix it.
2. The new `snapshot_scaling` cases never check the wait the step records. A filter engine that drops it passes all 9 cases (`L/snapshot_scaling_red_drop-new-wait.log`); the other three defects I injected all go red.
3. Nothing tests the client's own hello bound (`figram_client._check_hello`). The comment at `protocol_edges.py:593` says its rows match the Go tests; they no longer do. The Go duplicate-`need` fix is tested and goes red when reverted.
4. `host-contract.md:133-134` has a garbled sentence ("with outcomes: and `failed` is delivered"). Section 1 step 2 gives the dropped-outcome mark without the run-id note that section 2 has.

**Checked and consistent**
- **V5/V6 on the chart:** I ran the 7239ab7 `check` (code copied verbatim) against the 598fec6 one on 11,318 documents (9,668 decodable). Acceptance is identical everywhere.
  - 195 error lists differ only in how an empty-key node is shown (`` vs `bot`), in documents already rejected for the empty name.
  - 358 warning lists lost only timer-without-target warnings, and all 2,156 removed warnings name a message that does not own the timer.
  - A deliberately wrong old `check` turns red (`L/oldnew_check*`, `L/classify_diffs.log`, `L/verify_removed.log`).
- **Warnings fix:** `lint_warnings` passes on 598fec6; the 7239ab7 binary fails exactly the two new cases.
- **Proof gates** (`theorems.lean`, `Statements.lean`, `strict_gate.sh`, `controls.sh`) are green. The 99 single-line statements equal THEOREMS.expected and the `proofs.md` tables. Apart from P2-2, the statements say what the prose claims.
- **New engine rules in the docs:** 23 probes of the rules added to format.md and host-contract.md all hold, and the inverted control fails all 23 (`L/doc_claims*`).
- **host_sim reconciliation:** the contract host passes, and the new control fails for the right reason (users u6 and u8 get a second order).
- **`--directed` scenarios:** 3,808 of them on 42 documents match the independent oracle; the oracle with the bug `outcome-any-token` gives 39 differences.
- **Red-team recovery cases in `directed.py`:** green on the engine, red on defect d21.
- **`figram random`:** 1,200 documents, all valid, 1,751 messages with two timers.
- **`stay-reenter` classification:** the index maps to a variant without a target for all 223 such mutants.
- **Go relink:** the old command keeps a stale binary; the new one links the changed library.
- **`init_race`:** 300 functional runs and 40 TSan runs are clean; concurrent init, loads and first requests are still tested, and load-during-request stays covered by `lazy_race` and the stress test.
- **Builds and doc check:** `ffi/build.sh` exits 0 and `ci/check_docs.py` reports 0 problems.

**Not done:** a full `tests/compiler/run_all.sh` and a full `verify.sh` on 598fec6 itself (I ran the individual steps); I did not re-audit the merged `oracle.py`/`gen.py` from d4db4d2.

# ffl-go 0.3.0 audit (commit 51c2312): report

## Summary
- **P0, a real defect:** ffl-go encodes requests with Go's `json.Marshal`, which writes `&`, `<` and `>` as 6-byte escapes. The engine keeps new snapshots under 15/16 of the request limit in its own, unescaped encoding.
  - With an action named `<ping>&co`, the step fails with `too_large` instead of `snapshot_too_large`. After that, `Outcome` and `Restart` with the stored snapshot also fail with `too_large`, so the user is stuck.
  - This contradicts protocol.go:154-155, README.md:139-141 and the CHANGELOG.
  - A one-line fix (encode without HTML escaping) was tested on a copy: the engine chain matches on 346 of 346 calls and the module's tests pass.
- **P1, typed round trip loses map order:** the engine writes `resume` and `timers` in its own key order; Go maps write them sorted.
  - The engine cancels timers on `restart` and on recovery in the snapshot's key order.
  - So through ffl-go, Restart and recovered steps emit `cancel_timer` in a different order than the engine gives for the snapshot it wrote: 899 restarts and 317 recoveries in one run.
  - README.md:88 tells hosts to "Store it as the engine returned it"; the typed API cannot do that.
- **P1, missing tests:**
  - 12 of 18 proposed defect mutants survive the module's tests (2 of those 12 are equivalent in practice).
  - The survivors include Timer guards being dropped and `Outcome` rejecting action names with `:`. The golden data has no outcome whose action contains `:` and no timer step with guards.
  - They also include six reply-validator checks (at, v, resume, timers, hello states, need entries), the start_timer token check and the trailing-text check.
  - All 23 of `scripts/mutants.py` are killed.
- **P1, docs:** "a stale or repeated outcome changes nothing" (and the same for stale timers) is false when the snapshot's message was removed by a document update. The engine then restarts the user with `recovered:true`.
- **P2:**
  - README says `snapshot_too_large` means unanswered waits; it can happen with no waits under a small limit.
  - Invalid UTF-8 in events and snapshots is silently replaced with U+FFFD; the C ABI contract says it is an error.
  - `checkReply` accepts duplicate entries in `need`; both references reject them.
  - The Restart and Recovered comments are incomplete about `pending` and outcomes.
- **P3:**
  - `decodeJSON` misses trailing `}` and `]`.
  - `Result.MarshalJSON` key order differs from the engine's.
  - Smaller doc nits, and the Figram embedding.md still points at ffl-go v0.2.0.
- **Q1, differential validator:**
  - 99,248 valid engine replies from 44 documents: identical verdicts from all three validators.
  - 168,353 mutants of 211 kinds: 13,979 disagreements, all classified (one ffl-go gap, one ffl-go nit, intended differences, reference gaps). No panics.
- **Q3, host:** 32 goroutines, 1,555 scenarios.
  - 108,948 same-request comparisons with figram-engine: all equal.
  - Under `-race` with 6,794 reloads: 219,030 equal, 1,355 whole-step retries after `ErrClosed`, 0 races, no deadlocks.
- Every check has a negative control that goes red.

All paths below are under /home/user/figram-audit/agents7/fflgo/. Logs are in LOGS/; the corpora were gzipped after the runs (corpus/*.jsonl.gz, out/*.gz). The worktree /home/user/figram-audit-worktrees/a7-fflgo was never edited and `git status` is clean.

## Findings

### P0-1 · The request encoding breaks the `snapshot_too_large` guarantee; the user gets stuck with `too_large`
- **Claim:** protocol.go:154-155 says "the stored snapshot still works for the outcomes and [Bot.Restart]". README.md:139-141 and CHANGELOG 0.3.0 "Added" say the same.
- **Cause:** protocol.go:320 builds the request with `line, err := json.Marshal(msg)`.
  - Go escapes `& < >` as `&`-style sequences: 6 bytes instead of 1.
  - The engine bounds the new snapshot at 15/16 of the limit in its own unescaped encoding (format.md §8 "Errors"). ffl-go then sends the snapshot back longer than the engine measured it.
- **Evidence, failing case:**
  - `limit/auditlimit docs/amp.bot.json btn:ping '<ping>&co' 12000` (rebuild with `go build` in limit/): LOGS/10-limit-amp.log, exit 0.
  - Output: "step 251 refused: too_large … 11977 bytes as ffl-go encodes it"; "Outcome … too_large"; "Restart … too_large".
  - The same snapshot sent raw in the engine's encoding (8,247 bytes) restarts fine.
- **Evidence, step-by-step comparison:** `claims/auditclaims limit docs/amp.bot.json btn:ping 12000`: LOGS/17-limit-amp-cmp.log, exit 1.
  - The same requests as ffl-go sends match the engine on 254 of 254.
  - The engine's own chain differs on 3: the engine continues where ffl-go gets `too_large`.
- **Controls:**
  - The same document with a plain action name gives `snapshot_too_large`, and Outcome and Restart work: LOGS/10-limit-plain-control.log.
  - features.bot.json: 406 of 406 equal on both comparisons, LOGS/16-limit-features.log, exit 0.
  - Negative control, engine at a different limit: 36 differences, LOGS/18-limit-negctl.log, exit 1.
- **Why it matters:** this is exactly the pre-0.3.0 failure the engine rule was added to prevent. It needs names containing `&`, `<` or `>` (in actions, messages, sections or timers) and a snapshot near the limit (waits nobody answers).
- **Fix:** in `call`, encode with `json.NewEncoder` and `SetEscapeHTML(false)`, trimming the trailing newline.
  - Tested on mod-fix/: `snapshot_too_large` at 343 waits; Outcome and Restart ok; engine chain 346 of 346 equal (LOGS/21-limit-amp-fixed.log). That run exits 1 only because its "same request" side still encodes the old, escaped way.
  - The module's tests pass with the fix: LOGS/22-gotest-modfix.log, exit 0.
  - Residual risk, untested hypothesis: encoding/json always escapes U+2028 and U+2029 (3 bytes to 6). Reject those in names or add a margin.

### P1-1 · Typed snapshots lose the engine's `resume`/`timers` key order, and that changes later engine output
- **Claims:**
  - README.md:88 "Store it as the engine returned it"; protocol.go:25 "Store it as its JSON encoding".
  - host-contract §1.4 "the JSON the engine returned, unchanged".
  - The brief's Q2 requires an exact round trip.
- **Evidence, round trip:** LOGS/08-roundtrip.log (exit 0) and out/rt_analysis.txt.
  - Snapshots: 51,845 byte-identical; 24,262 differ only in map key order (resume 12,384; timers 5,445; both 2,455; the rest also with escaping); 4,314 differ only by HTML escaping; 0 other differences.
  - This happens in ordinary `figram random` documents too (random-*: 8,267 resume and 3,961 timers cases).
- **Evidence, engine behaviour:** LOGS/14-engine-probes.log: requests 31 and 32 (restart), 21 and 22 (recovery) and 11 and 12 (leftover timers) emit `cancel_timer` in the snapshot's key order.
- **Evidence, through ffl-go:**
  - LOGS/15-claims.log C7: the engine starts the user with `timers {"mid","alpha","zeta"}`; ffl-go stores `{"alpha","mid","zeta"}`. `Restart` then cancels alpha, mid, zeta, where the engine cancels mid, alpha, zeta.
  - Host run: 899 restarts and 317 recoveries with only the cancel order different (LOGS/11-host-plain.log); 1,796 and 627 under `-race` (LOGS/12).
- **Why it matters:** cancellations commute for a scheduler that follows host-contract §3, so this is not a data loss. But a Go host cannot store the engine's JSON unchanged, and its outputs differ from the engine's and the reference hosts' for the same history. This breaks golden and replay comparisons.
- **Fix:** keep the engine's raw snapshot text (for example a `json.RawMessage` inside `Snapshot`, sent back verbatim when unmodified), or use ordered `Resume`/`Timers` types. Otherwise document that order is not preserved and that `cancel_timer` order may differ.

### P1-2 · Claimed properties without a test (Q5)
- **Evidence:** `q5/my_mutants.py` runs `go test -count=1 ./` on copies (LOGS/20-mutants-proposed.log, exit 0; the unmutated control passes).
- **Surviving mutants:**
  - timer guards dropped. Timer is documented to take guards like Step; golden has 0 timer steps with guards.
  - Outcome refuses `:` in the action name. The spec allows it; golden has 0 such outcomes out of 18.
  - Reply-validator checks for: snapshot `at`, `v`, `resume` values, `timers`; hello `states`; `need` entries; start_timer `token`; trailing text. protocol.go:14 claims every reply is "checked strictly".
  - Equivalent in practice: snapshot loses `v`; Decide's repeat guard removed.
- **Killed:** seq, pending reversed, detached on every call, hello protocol read as states, SetMaxRequest, timers dropped.
- The module's own `scripts/mutants.py`: 23 of 23 killed (LOGS/19-mutants-module.log).
- **Fix:** add TestCheckReply rows for each of the surviving checks; add a golden or unit test of `Timer` with Need and `Decide`, and one of `Outcome` with an action containing `:`.

### P1-3 · "A stale or repeated outcome changes nothing" and "a stale timer changes nothing" are false after a document update
- **Claims:** protocol.go:245-246, protocol.go:232-233, README.md:112-113, README.md:121, CHANGELOG.md:19.
- **Evidence:** LOGS/15-claims.log, exit 0.
  - The controls hold: a repeated outcome, a stale token and a stale timer token on the current document change nothing.
  - C1: a repeated outcome with `at` set to a removed message gives `{"actions":[{"send":"menu"}],"recovered":true,…"seq":8}`.
  - C2: a stale timer in the same situation gives `cancel_timer` plus `send` with `recovered:true`.
  - The engine itself does the same: LOGS/14 requests 42 and 44. The spec says so too: format.md decision 8 ("Recovery happens for any event, including a timer event with a stale token") and rule 1 of a step.
- **Why it matters:** a host that skips storing or executing the result of a stale event (believing nothing changed) drops the recovery. I rank this P1 rather than P0 because it only happens with snapshots from an earlier document version.
- **Fix:** add "unless the snapshot's message is no longer in the document (Recovered)".

### P2-1 · README: `CodeSnapshotTooLarge` "means that the user has as many unanswered waits as the request limit allows"
- **Claim:** README.md:139.
- **Evidence:** LOGS/15 C6: `SetMaxRequest(71)` with `Step({"at":"home","seq":1}, "btn:go")` on docs/order.bot.json gives `snapshot_too_large` with no wait in either the old or the new snapshot. The new snapshot grows by `back`, `resume` and timers.
- **Fix:** say "usually waits nobody answers; the snapshot also grows with timers and remembered places".

### P2-2 · Invalid UTF-8 is silently replaced with U+FFFD by the typed API
- **Spec:** embedding.md "Strings": invalid input "is an error, never replaced with U+FFFD".
- **Evidence:** LOGS/15 C5 and C5b.
  - A raw request containing `\xff` gets `parse_error` from the library.
  - `Step(menu, "btn:\xff")` succeeds as the event `btn:�`.
  - A snapshot with `At:"men\xff"` is not rejected: it gives `recovered:true` and restarts the user.
- **Fix:** check `utf8.ValidString` on the event, action, outcome, timer, guard names and snapshot strings, and return an error.

### P2-3 · `checkReply` accepts duplicate `need` entries
- **Evidence:** LOGS/06-compare-mut.log row `need:dup`: ffl-go says `need` on 599 of 599; Go ref and Python say ERR.
- With a duplicate, `Decide` fails with the misleading "the engine asked again for condition".
- **Fix:** reject duplicates. The mutant that does this survives all module tests (LOGS/20), so the change is compatible.

### P2-4 · The Restart and Recovered comments are incomplete for protocol 3
- protocol.go:214-215 (Restart) does not say that it clears `back` and the waits. Outcomes of calls in flight are then ignored (LOGS/15 C4 shows Pending cleared). The CHANGELOG does mention it.
- protocol.go:118-121 says a recovered step processes the event "as [Bot.StartWith] processes it". But StartWith rejects outcome events, while recovery keeps the waits, processes an accepted outcome and starts the user over on a stale one (format.md §8 rule 1; LOGS/14 request 42).
- **Fix:** amend both comments.

### P3
- **Trailing text check misses `}` and `]`:** `decodeJSON` (protocol.go:347-357) accepts a reply followed by `}` or `]`, because `json.Decoder.More` returns false before them.
  - Evidence: LOGS/06, rows `raw:trail:brace` (1,807 of 1,807) and `raw:trail:bracket` (1,796 of 1,796). ffl-go and Go ref accept; Python rejects. No test covers it (mutant survives).
  - Fix: after `Decode`, require only whitespace up to EOF.
- **Result.MarshalJSON key order:** protocol.go:125 says "the protocol form", but it writes `actions, recovered, snapshot`; the engine writes `snapshot, actions, recovered`. This affects 80,421 of 80,421 result replies (LOGS/08). Names with `&<>` are also escaped (4,314 snapshots, 2,423 actions).
- **Action.Token on a Call without outcomes:** protocol.go:60 does not say the token is 0 and ignored in that case. `Action{Kind:Call, Token:7}` marshals without the token.
- **Outcome docs and `:` in action names:** the Outcome docs never say the action name may contain `:`. ffl-go handles it correctly: 17,262 such outcome requests in the corpus, and the host runs on 10 documents with `:` in names.
- **SetMaxRequest doc:** figram.go:58-61 mentions only `too_large`, not the 15/16 rule.
- **Figram docs (outside ffl-go):** docs/embedding.md "Go" still says `go get …@v0.2.0` and "until the ffl-go version for this release is published".

### Differential classes that are not ffl-go bugs (Q1)
- **Intended differences:**
  - `mutant` in hello is rejected (44 cases).
  - hello protocol other than 3 passes `checkReply`; `Load` rejects it through `protocolMismatch`, which is tested (44+44).
  - `id:null` with `parse_error`/`too_large` is accepted. The library emits these; Python rejects them because it models the process (229+231+65).
- **Go reference gaps:**
  - It accepts any trailing text: rows `raw:trail:x`/`num`/`obj`, about 5,300.
  - It accepts a result reply to `hello`: LOGS/23-crossop-table.txt, `result@hello`. ffl-go and Python reject it.
- **Python reference gaps:**
  - It accepts `-0` as a token, seq or timer value (3,594 cases); both Go validators reject it, and the engine never writes it.
  - It rejects an empty error text (233 cases), stricter than both Go validators.

## Checked and found consistent
- **Q1, valid replies:** 99,248 engine replies from 44 documents: 3 examples, docs/order.bot.json, 20 `figram random` documents, and 20 transformed ones with `:`, `é` and `&<>` in names.
  - Variants: 80,421 result, 18,163 need, 620 error (all 6 codes, including 2 `snapshot_too_large` and 220 `parse_error`), 44 hello.
  - Content: 14,651 recovered; 52,442 with `pending`; 5,035 calls with token, 5,352 with token and detached, 4,900 detached only.
  - All three validators agree on every reply (LOGS/04, exit 0).
- **Q1, mutants:** 168,353 mutants of 211 kinds. 134,831 rejected by all three; 19,543 accepted by all three, all legitimately valid forms (27 kinds). ffl-go never panicked in the conversion code; Python never crashed (LOGS/06). Negative control, a broken copy that accepts `detached:false`: 17,641 disagreements with new rows (LOGS/07).
- **Q2, round trip:** 0 lost fields, 0 reordered `pending`, identical presence of `token`/`detached` across 96,956 actions. All differences are map order or HTML escaping. Controls: corrupting detached or reversing pending gives 10,257 and 23,047 unexplained differences (LOGS/09).
- **Q3, host:**
  - Coverage: Start and StartWith with Decide; Step; Timer; Outcome, including repeated, stale, token 0, `failed`, recovered and recovered-stale outcomes and an outcome for an action without outcomes; stale timers; Restart; recovered steps.
  - Results: 108,948 same-request comparisons equal, 0 different (LOGS/11). Under `-race` with reloads: 219,030 equal, 0 races, no deadlock (watchdog), 1,355 retries after `ErrClosed` (LOGS/12). Negative control: 85,176 differences, exit 1 (LOGS/13).
- **Q4:**
  - Examples and the README quick-start output match the tests (LOGS/00).
  - Outcome with token 0, with an action without outcomes, with `failed`, and refusing `:` in the outcome before sending all behave as documented (LOGS/15 C3a-d).
  - Concurrency and Close behave as documented (LOGS/12).
- **Q5:** module tests pass (LOGS/00); 23 of 23 module mutants are killed (LOGS/19).

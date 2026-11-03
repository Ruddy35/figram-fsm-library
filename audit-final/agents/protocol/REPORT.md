# Auditor `protocol` (final audit): protocol 3, host contract, clients, schemas, C ABI

Worktree `/home/user/figram-audit-worktrees/a5-protocol` at 7239ab7. Brief: `tools/BRIEF5.md`.
The harness did not let the agent write REPORT.md; this is its final message, recorded verbatim by
the orchestrator, with one exception: item P3 (11) described a performance class that is tracked
in the project's private security advisory (SECURITY.md: no public reports of vulnerabilities);
its text is replaced by a reference. Scripts, inputs and logs are next to this file (`LOGS/`
and the other directories; compiled binaries are excluded, see `../../MANIFEST.json`). The
orchestrator's handling of each finding is in `../../FIXES.md`.

---

protocol audit of Figram 0.3.0 (worktree a5-protocol @7239ab7; no product file changed, git status is clean apart from ignored build output and __pycache__). I did not write REPORT.md: the harness blocks subagents from writing report files, so the full report is this message. Scripts, inputs and logs are in /home/user/figram-audit/agents5/protocol/ (L = LOGS/). Every log records the command and exit code.
Answer: the engine, both schemas, the Python and Go validators, the C ABI and the process agree on everything I generated: about 70k replies, 0 disagreements on valid replies. No P0. The gaps are in docs/host-contract.md and in what host_sim exercises.
P1-1 `failed` is defined as "could not be performed: no answer…" (format.md:398-400, host-contract.md:133, CHANGELOG:21), but a timeout does not mean the action was not performed. With the contract's keys and the pizza retry this double-orders. failed_unknown.py runs host_sim unchanged except for a CRM that creates the order and loses the answer: u4 ends in done with 2 orders (L/failed_unknown.log). host_sim's CRM raises before acting (host_sim.py:180-185), so it cannot see this. Fix: `failed` means "outcome unknown"; reconcile by the call's key before delivering it, or retry under a business key.
P1-2 Outcome events have no rollback-safe update_id. host-contract.md:62-77 says the recommended keys are never reused after a rollback, but outcome events get `call:<action>:<token>` (line 134), with no run id as timers have (lines 70, 177-181). After a rollback the reissued seq makes a late outcome of the lost branch acceptable to the new wait: acceptance depends only on (action, token) (Core.lean:150-153; L/outcome_edges_pizza.log ids 5-6). The actions it triggers then reuse executed keys. Hypothesis about host behaviour; rollbacks are unmodelled (host-contract.md:394). Fix: a run id per call.
P2-1 Outcomes race with the user's own updates. Once the barrier releases, a queued Cancel, /start or /help can run first. At menu, `created` is then accepted with no arrow: wait removed, `actions: []`, no "done", no notify_kitchen (L/outcome_edges_pizza.log ids 2-4). `figram check` gives no warning on pizza. host_sim users never act while a wait is pending (host-contract.md:361), so moves while waiting, latest replacement, `every` and detached outcomes are never exercised. Fix: process a user's outcome events before their later updates, put outcome arrows on the section or root in pizza, and add such a host_sim scenario.
P2-2 host-contract.md:121-125 sends a failed background action to the failure queue; lines 118-119 and 129-139 say actions with outcomes deliver `failed`. For a detached action with outcomes (features `ping`, every) the literal reading never delivers `failed`, so the wait stays forever.
P2-3 An outcome event for a user without a snapshot is not covered by the contract (only timer events, host-contract.md:46-48). `start` gives bad_request; the section-4 placeholder route starts the user over with recovered:true (L/client_outcome_recovery.log). host_sim.py:28-29 claims it drops such events, but its scenario drops only timer events: {'timer': 6} (L/lost_dropped.log).
P2-4 An undeclared outcome name (`call:create_order:bogus` with a valid token) is accepted: the wait is removed, nothing happens and seq stays the same, so the user hangs (L/outcome_edges_pizza.log id 1). This is undocumented, and Engine.outcome checks only the shape.
P2-5 host-contract §7's "not modelled" list omits: document updates (§6), outcome events without a snapshot, the "record it first" ordering, detached actions with outcomes, and answers lost after execution.
P3:
(1) Validator mismatches on 45 malformed replies (L/malformed_python.log, L/malformed_go.log exit 1). Python accepts hello `states` -1 and 2^64 (protocol.py:142-144, figram_client.py:150). Go accepts duplicate `need` names. Both accept duplicate JSON keys. The client accepts unknown error codes.
(2) Engine.outcome docstring (figram_client.py:803-805) is wrong in recovery.
(3) format.md:96 omits pending tokens from the exact-integer fields; format.md:549-551 says "all call actions" where the contract says sequential.
(4) format.md:690 "four errors" omits the shim's bad_request.
(5) format.md:462 "accepts exactly what the schema allows": 0e1001, a lone `\ud800` and duplicate keys are rejected with parse_error, and the surrogate case is missing from the error table (L/schema_vs_engine.log, 8 disagreements, 2 of them documented).
(6) `every` waits grow without bound: the engine returns a snapshot it then rejects as too_large (L/pending_growth.log), contrary to the general reading of format.md:565.
(7) seq is not a snapshot version: an outcome with no arrow changes the snapshot at the same seq. host-contract.md:61 should warn that a host mapping snapshots to its own records can drop `pending`.
(8) Wording: host-contract.md:141 ("its outcome follows them") is ambiguous; host-contract.md:377 says "after a restart" but the scenario uses no restart; how long outcome marks are kept is unstated.
(9) embedding.md:250 recommends ffl-go@v0.2.0, which rejects protocol 3; README.md:88 still has VERSION=0.2.0.
(10) No `pending` case in snapshot_scaling. Measured linear: ratios 3.8-5.4, at most 0.21 s CPU for 60k waits; 35,317 waits fit the 1 MiB limit; a 1 MiB request works on a 64 KiB C thread (L/pending_scaling.log, L/capi_bigstack_*).
(11) [Not published: a performance class of the JSON text layer that is tracked in the project's private security advisory. It concerns input from the host, not from users.]
Checked and consistent:
- Random walks over 43 documents (examples plus `figram random 1..40`), 52,890 requests: schema, check_reply, and token/detached against the declarations, tokens equal seq, waits correct (L/walk_random.log).
- Go checkReply copied verbatim: 0 rejections (L/go_validator_engine_replies.log).
- Process and C ABI replies byte-identical on 9,852 requests (L/parity_process_vs_capi.log).
- `need` is sufficient on 6,498 requests, 405 of them outcome events (L/need_check.log).
- Cross-document recovery: 3,360 requests, 1,156 recovered (L/cross_doc.log).
- Repository tests: protocol_edges 250/0, snapshot_fixtures 49/0, client_contract, C protocol3, Go TestFixOutcomes, gofig golden; check_docs.py reports 0 problems.
- host_sim exits 0. All 5 built-in controls go red for the right reason, and --no-barrier goes red (exit 1) (L/host_sim*.log).
- Timer marks in host-contract §1.2 and §3 agree with each other and with the engine (L/timer_marks.log).
- CHANGELOG numbers check out: 70 pinned statements, 37 golden scenarios, 4 new mutants.
Side note: a log path with an unset variable made me create an empty /LOGS at the filesystem root. Its files were moved into LOGS/, but the safety check blocked removing the directory; please run `rmdir /LOGS`.

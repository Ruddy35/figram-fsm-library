REPORT.md could not be written: the harness refuses report files from subagents. BRIEF6 says to put the whole report in the final message in that case, so it follows in full. The usual 40-line summary is the first block.

**Summary (hostpy, Figram 0.3.0 @ 598fec6, worktree clean)**

Question: can a developer who follows `docs/host-contract.md` and `docs/format.md` build a correct protocol-3 host with the Python stack?

- **Yes, for normal operation.** I wrote a host from the two documents only; `tests/engine/host_sim.py` was read afterwards, just to compare. It uses `figram_client`, SQLite in WAL mode and separate executors.
- **Green runs:** about 76k steps in the scenario matrix and about 240k more in the soaks, with crashes injected at all 10 boundaries, duplicate updates and outcomes, snapshot loss, `restart`, a document update and a database restore. All 8 invariant groups held.
- **Negative controls:** all 12 deliberately broken hosts went red.
- **Where the documents fail:**
  - **P1-1** Reconciling "by idempotency key" as a plain read races with a request the external system has accepted but not yet applied. The user is told `failed`, the order is created later, and the retry makes a second order: `t1-read` exit 1, 6 users; with a fence instead of a read (`t1-fence`) exit 0.
  - **P1-2** A restore with a scheduler outside the database loses every timer that fired between the backup and the restore. Section 3 discourages the only repair (re-arming from the restored state): `s3-fired-timer-outside` exit 1; with re-arm, or with the scheduler inside the database, exit 0.
  - **P1-3** After a rollback, the reissued token collides with the lost history's scheduled event under both storage rules of section 3. The new start is refused (pair rule) or ignored (triple), and the old event is then dropped by the run-id rule: `s2-pair` and `s2-triple` exit 1; with a purge or run-aware replace, exit 0.
  - **P1-4** The run-id check needs "the current call", but a host that deletes executed outbox rows (section 4 allows it) drops every outcome and every timer: `t5-gc`, 592 drops, 60 of 60 users stuck.
  - **P1-5** "back" or "resume" returns users to a "please wait" message after its outcome was handled elsewhere. `examples/pizza.bot.json` has this trap. In my booking bot it is absorbing: 92 of 100 soak users could never book again. `figram check` says ok.
  - **P2-6** For a sequential action with outcomes, "please wait" is sent only after the call has finished (197 of 197 cases).
  - **P2-7** Read literally, "record the outcome in the transaction that marks the action executed … goes through section 1" deadlocks with the barrier.
  - **P2-8** After a restore, confirmed updates never come back. Orders made in the lost history are orphaned, and 5 of 10 affected users ordered the same cart again.
  - **P3-9** Section 6 gives no advice when a new version removes an action's outcomes: users waiting for it stay put.
  - **P3-10** Client friction: `from protocol import`, process failures carry no error code, engine pids are only reachable through private attributes.
- **Performance:** the engine processes stay at 6.2–6.5 MB and use about 1.5 % of the host's CPU. This Python/SQLite host peaks around 310 steps/s, with 50–58 MB RSS.

Files are in `/home/user/figram-audit/agents6/hostpy/`: `LOGS/<run>.log` holds each command and exit code, `runs/<run>/summary.json` the full result.

---

**Full report**

**1. What I built** (everything in `/home/user/figram-audit/agents6/hostpy/`)

- **`host.py`:** one process per bot document.
  - Engine: `figram_client.Engine(processes=2)`.
  - `host.db` (SQLite WAL) holds the snapshots (stored verbatim, also when `seq` did not change), the processed ids, and an inbound queue for Telegram updates (getUpdates offset in the same transaction), timer firings, outcome events and admin operations.
  - The outbox stores `seq`, position, token, `detached`, the idempotency key `user:<source uid>:<action>:<occurrence>`, a run id, the event data and the commit time.
  - `host.db` also holds the scheduler, the host's own data read by conditions, the failure queue and an event log for the checker. The high-water mark lives in `hwm.db`, outside the database that can roll back. `sched.db` is an optional external scheduler.
  - Per-user claiming; the barrier excludes background actions.
  - 16 partitioned sequential executors; 3 background executors with backoff, a failure queue and alerts.
  - The outcome is recorded in the transaction that marks the call executed. Reconciliation uses a fence by default.
  - Run-id checks on timer and outcome events. `start` with the update's event and the high-water mark for users without a snapshot; `restart` under the user's lock.
- **`ext.py`:** simulated systems, idempotent by key.
  - CRM: created, rejected with a reason, 5xx, timeout before acting, timeout after acting (answer lost), or a request queued inside the system and applied later.
  - Bank and venue.
  - E-mail and notification services: down for a while for everyone, down for good for 15–20 % of users.
  - `lookup` and `fence` by key; webhook callbacks of results with the host's run id.
- **Bots** (`figram check`: ok):
  - `shop`: a latest-wins action with outcomes, outcome arrows on the waiting message, a section and the root, background actions, and arrows without a target on timers and sections.
  - `payouts`: `every` transfers behind an arrow without a target, plus a funds condition that needs the barrier.
  - `booking`: a background action with outcomes and `every`, plus `resume` and `back`.
  - `shop_v2` and `shop_v2_sync` for the document update.
- **`run.py`:** users who see only the chat; they double-tap, press buttons while a call is running, open /help, retry after `failed`, and type /start when nothing happens. 10 % of updates are delivered twice at once and 5 % again later. The host is a child process killed by `os._exit` at named boundaries and restarted. An operator loses snapshots, resets users, restores a backup and redeploys v2.
- **Scripted single-user runs** (`scripts/`) make the rollback cases deterministic.

**Invariants** (`check.py`), checked after quiescence:

| | invariant |
|---|---|
| I1 | ≤ 1 positive effect per logical action (identity independent of the key); no effect behind a `failed` or negative outcome; a positive outcome belongs to the effect of the same logical action |
| I2 | every call with a token is executed and has exactly one processed outcome; "not accepted" only if the wait was replaced, cleared or the snapshot was lost; no accepted outcome or timer from another history |
| I3 | no wait left in a snapshot; every started timer fires unless the user left before it was due; users on a waiting message without a wait are classified (host fault vs document trap) |
| I4 | each delivery processed once; every step read the snapshot the previous step wrote; final snapshot equals a replay through the engine |
| I5 | no event waited for a background action |
| I6 | no protocol violation; every logged reply re-validated with `protocol.py` |
| I7 | no condition computed with sequential actions pending; no empty order; no overdraft |
| I8 | nothing left unexecuted; failure queue holds only background actions; no deadlock |

**2. Findings**

**P1-1. Reconciliation as a read races with an in-flight request.**
- Claim: host-contract.md:143-149, "ask the external system for the result of the call by its idempotency key … and deliver that outcome if it acted".
- Evidence: with 6 % of CRM requests applied 0.4 s late, `LOGS/t1-read.log` exits 1 with `I1.told_failed_but_acted 6`. `t1-fence` (same load) exits 0. `n4-no-reconcile` also exits 1 with 6, so the read-only host does no better than no reconciliation here.
- `host_sim` reconciles with `crm.lookup` and its CRM never applies a request late, so its control cannot see this.
- Fix: reconciliation must close the key (fence or cancel by key, or wait out the system's deadline). Say that a read is safe only for synchronous systems; otherwise use a business key.

**P1-2. A restore against an external scheduler loses timers that fired after the backup.**
- Claims: :72-75 ("keeps [the run id] with the scheduled event"), :203-206, and :217-218 ("rebuilding the scheduler from [snapshot.timers] brings back timers that already fired").
- The timer was started before the backup and fired after it. The scheduler forgot it; the record of the firing was rolled back; the restored snapshot still holds the timer, so nothing will ever fire it.
- Evidence:
  - `LOGS/s3-fired-timer-outside.log` exit 1 (`timer_lost`, `timer_never_fired`).
  - `s3-fired-timer-inside` exit 0; `s3-fired-timer-outside-rearm` exit 0.
  - Under load: `t2-sched-out-triple-norearm` exit 1 (`timer_never_fired 13`); `g6-sched-out-triple-rearm` exit 0.
- Fix: document a restore procedure. Re-arm every timer the restored snapshot holds whose `start_timer` row was restored, whose firing mark (with run id) is neither processed nor queued, and which the scheduler does not hold. Or keep the scheduler in the restored database.

**P1-3. Reissued tokens collide with the lost history's event under both storage rules.**
- Claims: :178-188 (the pair rule "only if its token is greater", or "the triple (user, name, token)"), and :76-78 (rollbacks reissue tokens).
- Evidence: `LOGS/s2-rollback-timer-pair.log` and `s2-rollback-timer-triple.log` exit 1. `s2-rollback-timer-pair-purge` and `s2-rollback-timer-triple-replace` exit 0. Under load, `t2c` exit 1 (one refused start) and `g7-sched-out-pair-rearm-purge` exit 0.
- Fix: include the run id in the scheduler key (a start with another run id replaces the stored event), or purge events whose run id is not in the restored database.

**P1-4. Outbox clean-up and the run-id check are incompatible.**
- Claims: :75-78 and :203-206 look up "the current call" or "the id stored in the database"; :262 allows deleting executed rows.
- Evidence: `LOGS/t5-gc.log` exit 1: `drop.stale_run 592`, `wait_left_in_snapshot 60` (60 of 60 users), `timer_lost 43`.
- Fix: say that (user, action, token, run id) is kept until the outcome is processed, and the timer's run id while the snapshot holds the timer.

**P1-5. "back" and "resume" lead into a waiting message whose wait was answered elsewhere; the trap can be absorbing.**
- Claims: :161-166, and format.md decisions 4 and 10.
- Evidence:
  - `LOGS/repro-pizza-back-into-placing.log` exit 1: the order is created, the user is not told, and stays on "please wait" for good.
  - `LOGS/repro-booking-resume-absorbing.log` exit 1: cancel then book lands on the dead `confirming` every time.
  - `g3-booking`: 44 of 150 users trapped; `soak2-booking`: 92 of 100. The host invariants stay green; these users are classified as a document trap.
- Fix: document that a waiting message must not be a remembered place for resume or back (or must have a way out that does not depend on the outcome). `check` could warn about this; the pizza example's outcome arrows should move to the root.

**P2-6. "Please wait" arrives after the wait.**
- Claims: format.md:329-332 (action order) plus host-contract.md:115-117 (execution order) imply it; :158-163 and the pizza `placing` message assume the opposite.
- Evidence: `LOGS/analyze-wait-g1-shop.log` 72/72 and `analyze-wait-g3-booking.log` 125/125 waiting messages were sent after the call finished (commit to call finished: p90 0.9–1.2 s, max 2 s).
- Fix: say so, and give a pattern (call the action from a later step, or make it a background action, which today conflicts with :134-137).

**P2-7. The literal reading deadlocks.**
- Claims: :153-159 together with the barrier at :28-35. Section 2 never names the inbound queue.
- Evidence: `LOGS/t3-outcome-inline.log` exit 1, `deadlock.outcome_inline 19`.
- Fix: "record the outcome in an inbound queue; a worker processes it under the user's lock".

**P2-8. A rollback loses confirmed updates and orphans their effects.**
- Claim: :81-84 relies on redelivery, which Telegram does not do for confirmed updates.
- Evidence: `LOGS/analyze-restore-g4.log`: 3002 updates from 119 users were never processed again. 10 orders were orphaned, and 5 of those users ordered the same cart again.
- Fix: state the limit; a restore needs reconciliation with the external systems since the backup and a message to the affected users.

**P3-9. Document update removing outcomes.** :372-375. `LOGS/repro-doc-update-drops-outcomes.log` exit 1: the user stays on `placing`. Fix: add a sentence like the one for timers ("restart them").

**P3-10. Client friction.**
- `figram_client.py:59` does `from protocol import …`: the client is not a package, so `clients/python` must be on `sys.path` and the host cannot have its own `protocol` module.
- Process failures arrive as `EngineError(code=None)`, so a host must match the message text to alert on a broken engine. :319 says to decide by code, not text.
- Engine pids are only reachable through private attributes (`Engine._procs[i]._p.pid`).

**3. Runs**

Green (exit 0):
- Main matrix: `g1-shop`, `g2-payouts`, `g3-booking` (150 users each, 10 snapshot losses and 10 resets), `g4-restore`, `g5-restore-booking`.
- Scheduler, update and reconciliation variants: `g6-sched-out-triple-rearm`, `g7-sched-out-pair-rearm-purge`, `du-shop` (v1→v2: 2 recoveries, 49 firings of the renamed timer ignored), `t1-fence`.
- Targeted crash-point runs: `g8-crash-points`, `g9-crash-callback`.
- Scripted rollback cases: `s1-rollback-outcome`, `s2-…-pair-purge`, `s2-…-triple-replace`, `s3-…-rearm`, `s3-…-inside`.
- Soaks: `soak-shop`, `soak2-shop`, `soak2-payouts`, `soak2-booking`.

Crashes by boundary, matrix plus targeted runs: before commit 219 (while processing an outcome 8, a timer 13); after commit 188; between the external answer and recording it 131; background 12; getUpdates 26; external scheduler 4; webhook poller 8.

Negative controls (all exit 1):

| control | went red with |
|---|---|
| n1-no-user-lock | I4 stale snapshot / replay differs 136 |
| n2-random-keys | `duplicate_effect` 9 |
| n3-outcome-after-mark | `outcome_never_processed` 11 |
| n4-no-reconcile | 6 told failed but acted |
| n5-bg-in-barrier | I5, plus 20 outcomes never processed |
| n6-no-barrier | `overdraft` 6 |
| n7-no-runid-check | `timer_of_another_history` 3 |
| s1n-rollback-outcome-no-runid | a lost-history "rejected" was accepted while the order was created |
| n8-keys-seq | `duplicate_effect` 1 |
| n9-store-only-if-seq | stale snapshot 10 |
| n10-bad-engine | I6 |
| n11-no-dedupe | processed twice 248 |

**4. Performance**

| soak (400 s, crash p = 0.0003) | steps | steps/s | update latency p50 / p99 | host RSS max | engine RSS max | CPU host / engines |
|---|---|---|---|---|---|---|
| soak2-shop (100 users, start over after each flow) | 124 169 | 310 | 104 / 1347 ms | 57.8 MB | 6.45 MB | 616 / 9.2 s |
| soak2-payouts | 63 264 | 158 | 31 / 1210 ms (outcomes 734 / 3935 ms) | 49.5 MB | 6.4 MB | 688 / 5.0 s |
| soak2-booking | 46 934 | 117 | 10 / 361 ms | 50.3 MB | 6.4 MB | 540 / 2.9 s |

- Low load (`soak-shop`, 150 users finishing once): p50 30 ms.
- The bottleneck is this Python/SQLite host: polling threads, about 8 write transactions per step, and WAL checkpoint starvation (one WAL file reached 288 MB in 40 s). It is not the engine.
- Under the overload in the matrix (150 eager users, a crash about every 0.6 s), update latency reached a median of 11–25 s.

**5. Checked and found consistent**

- Duplicate filtering, exactly-once outcomes (executor and webhook share one uid), the barrier and conditions, background actions never blocking a user, `failed` for background actions with outcomes, the high-water mark plus `start` with the update's event after a lost snapshot, outcome and timer events without a snapshot dropped and marked, `restart` with waits pending, recovery after the document update, storing the snapshot when `seq` is unchanged.
- Run ids protect lost-history outcomes (s1 vs s1n). `user:seq:index` keys re-execute a redelivered update after a rollback, exactly as section 1 step 4 says (n8).
- Not covered: shared scarce resources, the Go client and C ABI, forged timer events (impossible by construction here), real Telegram.

**6. Files and caveats**

- Files: `host.py`, `ext.py`, `run.py`, `check.py`, `badengine.py`, `bots/`, `scripts/`, `repro/`, `matrix.sh` + `matrix-rest.sh`, `soak.sh`, `scen.sh`, `analyze_*.py`, `table.py`. Output is 12 MB.
- Run databases were deleted after their summaries were written, to stay under the disk limit. The scripted runs keep theirs.
- After the container restart I re-ran `n4` onward; the earlier runs had finished and are logged with exit codes.
- The `soak-booking` run that crashed on a bug in my driver (KeyError) was fixed and re-run as `soak2-booking`.
- `LOGS/t1-read.log`, `t2c.log` and `t5-gc.log` were recorded before the matrix with the same arguments and are used instead of the matrix copies.

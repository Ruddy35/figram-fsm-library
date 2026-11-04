I couldn't save REPORT.md: the harness blocks report files here, so the full report is below the summary, as BRIEF6 says to do in that case.

**Summary (hostgo, 598fec6)**

The engine behaved correctly on every step I ran. The host's step log replays step by step and matches across all green runs: 80,929 steps, 2,001 to 2,385 users, about 1,000 crashes at 14 boundaries, 1,879 reloads, 13,800 outcome steps, the race detector, and up to 29,000 `every` waits. No reply failed the reference validator. A careful Go developer can build a correct host from the documents, but the six P1/P2 items below are traps or dead ends.

- **P1-1 Memory grows without bound on large requests (new).** Resident memory grows by about 1.5 bytes per wait per request: roughly 2 KB, 6 KB and 22 KB per request at 1,000, 4,000 and 16,000 waits; nothing at 0 or 100. This happens both in `figram-engine` and through the C ABI, with a constant thread count and flat Go and malloc heaps.
  - The 10-minute soak went from 14 MB to 607 MB and was still 525 MB after every machine was unloaded (exit 1).
  - With `MIMALLOC_ARENA_RESERVE=1GiB` it stays flat and the soak passes. The cause is the zero arena reservation in `ffi/figram_rt.c` lines 22 and 33. The memory section of `embedding.md` (lines 209–233) only measures small requests.
- **P1-2 A user can be wedged by the request limit.** The engine accepts a 1,048,557-byte request and writes back a snapshot of 1,048,542 bytes. Every later request carrying that snapshot is rejected as `too_large`, including the outcome that would shrink it and `restart`. A host that answers every call still gets there when calls outpace outcomes. `host-contract.md` line 322 classes `too_large` as "a bug in the host's request", so such a host sends all of that user's events to the failure queue.
- **P2-1 No importable Go wrapper for protocol 3.** `clients/go` is a `main` package; importing it fails with "is a program, not an importable package". The `ffl-go` module v0.2.0 only speaks protocol 2. The instruction in `embedding.md` lines 278–287 cannot be followed as written; I had to copy the wrapper and rename the package.
- **P2-2 `Bot.Request` reports `need` and engine errors with a nil error.** They come back as a separate variant string, which `embedding.md` doesn't describe. A host using the usual "check err" idiom stores the snapshot as `null` in all four cases I tried.
- **P2-3 Reloading through the wrapper needs an undocumented retry.** A goroutine still holding the old machine gets an untyped `"bot is closed"` error. The fix is to repeat the whole step, including the condition round trip, on the new machine. That worked 23,794 times with no wrong result, but the documents never mention it.
- **P2-4 "Resume" or "back" can strand a user on a "please wait" message.** The user re-enters it after its wait is gone and no outcome will ever move them. This reproduces with the official `pizza.bot.json`: /help from `placing`, the outcome arrives on `help` and is ignored, "back" returns to `placing`. Both documents pass `figram check` with no warning. It showed up in the big run, and probably explains why the 3,000-user attempts ran past 15 minutes (a hypothesis).
- **P3 items:**
  - The reference validator rejects the shim's documented `bad_request` replies with a null id.
  - Reloading a document that changes an action from `every` to latest-only drops all older waits on the next call.
  - Swapping and unloading a machine on the raw C ABI while other threads use it crashed with SIGSEGV within 0.2 s in 3 of 3 runs. This is documented as undefined behaviour; the unsafe window covers the whole UTF-8 check of the request.
  - The engine costs about 63 ns per request byte (2 ms at 1,000 waits, 49 ms at 24,000), and the Go wrapper adds about 1.6×.
- **Negative controls, each red:**
  - No idempotency keys → I1.
  - No reconciliation → I1, a second order for the same cart.
  - No barrier → I4.
  - Background actions run inline → I5 (41% vs 3%).
  - Outcome recorded outside the transaction → I1, I2, I3.
  - No per-user lock → I2.
  - Engine reply without `pending`, or a call without its token → I1, I2, I3 (the validator can't see these).
  - `detached: false` in a reply → I6.
  - Race-detector canary → exit 66.
- **Green runs:**
  - The same scenario as the controls passed.
  - All 13 single-boundary crash runs passed. At probability 0.2, the reload crash starved 300 ms background calls (a liveness limit of the injection rate, not a safety failure); I reran it at 0.03 and it passed.
  - All boundaries with reloads: passed.
  - 2,001 users with reloads and duplicate deliveries: passed in 140 s.
  - The race-detector build: passed with 0 races.
  - Lean stack use stays at 8 KiB whatever the number of waits.

Evidence is under `/home/user/figram-audit/agents6/hostgo/`: logs in `LOGS/`, run results in `results/`, code in `host/`, reproductions in `repro/`, bots in `bots/`, scripts in `scripts/`.

---

# Full report (Figram 0.3.0 at 598fec6, auditor hostgo)

**Question.** Does the embedded engine, called through the C ABI from cgo, behave correctly and efficiently under a production-like Go host for protocol 3? And can a Go developer following the documents build a correct host?

## 1. What I built

All my code is in `/home/user/figram-audit/agents6/hostgo/host`, written independently of the repository's tests.

### Engine bindings

1. **The reference wrapper, reused unchanged.**
   - `sync_wrapper.sh` copies `clients/go/main.go` into a package called `refwrap`, changing only the `package` line, because the original can't be imported (P2-1).
   - My own `refwrap/export.go` exposes the reference validator and its reply-corruption hook for the controls.
   - It is built the way `embedding.md` describes for "your own module": `CGO_CFLAGS` and `CGO_LDFLAGS` come from the environment, and `--gc-sections` is accepted from there.
   - Every engine call the host makes goes through `Bot.Request`, so every reply passes the reference validator.
2. **My own minimal binding, `rawfg`.** Bytes in and out, no locking, plus a glibc `mallinfo2` readout. I use it for engine-only timing and for probing the C ABI.

### Storage

SQLite through `modernc.org/sqlite` v1.34.5, a pure-Go driver that was in the offline module cache.
- WAL mode with `synchronous=NORMAL`. That is durable against process crashes, which is what I inject; the benchmark also writes with `FULL`.
- One writer connection and a small reader pool.
- Tables:
  - `users`: snapshot stored as the engine returned it, a version column, and the high-water mark.
  - `processed`, `inbound`, `outbox` (seq, position, key, run id, due time, data, state), `timers`.
  - `effects`, with a unique key, so the host's own database actions apply exactly once.
  - `kv`: the data conditions read.
  - `steplog`: pre and post snapshot, guards, document version, barrier wait.
  - `drops`, `failq`, `outcome_log`, `ingest_log`.

### Host (`host.go`, `exec.go`)

- **Ingest.** An update is written to the inbound queue before it is acknowledged.
- **Stepping.** One step worker per user, in strict order. Duplicates are filtered by update id. The barrier counts the user's unexecuted sequential actions. Conditions are computed from `kv` and the event's data. Each step commits in one transaction.
- **Keys.** `user:update_id:action:occurrence`, with update ids `tg:<n>`, `timer:<name>:<token>:<run>` and `call:<action>:<token>:<run>`.
- **Dropped events.** An event whose run id doesn't match is dropped. So is a timer or outcome event for a user with no snapshot; it is marked processed.
- **Executors.** A sequential executor per user, plus 256 background workers. A call with outcomes produces exactly one outcome event, recorded in the same transaction that marks the call executed. After three failed attempts the host asks the external system by key before giving up, and only then sends `failed`.
- **Timers.** A start only replaces a smaller token; a cancel only removes tokens below the cancelling reply's seq.
- **Support operations.** A reset runs `restart` on the latest snapshot. Deleting a user's data keeps the high-water mark; the user comes back with a button and is started with that event and `seq`.
- **Reload.** Load the new version, swap an atomic pointer, then close the old machine while other goroutines may still hold it. A step that hits `"bot is closed"` is repeated whole on the new machine. The current version is persisted.

### Crash injection

There are 14 crash boundaries. A crash abandons the whole host instance: uncommitted transactions roll back, in-memory state is dropped, and a new instance recovers only from the store. Telegram redelivers anything not yet acknowledged.

### Simulated external systems

They live outside the host, survive its crashes, are idempotent by key, and record the ground truth per logical action (and per cart for the CRM).
- **CRM:** created, rejected with a reason, server error, timeout before acting, timeout after acting. For 15% of users it acts and then loses the answer three times in a row, which forces the host to look the order up by key.
- **Loyalty and booking services:** the same failure modes.
- **Notification and audit service:** 30% of first attempts fail, 10% of users fail for good, and 20% of users have slow calls.
- **Telegram:** delivers at least once.

### Bots

All six pass `figram check` with no warnings (`LOGS/check-*.log`).
- **shop:** `create_order` with outcome arrows on the waiting message and on the root. A background `notify` with outcomes and `every`. Arrows without a target on the root for outcomes, on a message timer, and on a section timer. Resume and back.
- **points:** `award` with `every`, called by an arrow without a target, so waits pile up. The `enough_points` condition reads the effect of `credit`, which an outcome arrow triggers. A background `audit` with outcomes. Empty variants `{}`.
- **booking:** `reserve` is latest-only, with an "again" self-loop that replaces the wait. Outcome arrows on the waiting message, the section and the root. A section arrow without a target. Timer arrows with and without a target. Resume and back.
- **v2 versions:** rename the waiting message, a timer, or a message, so users can be waiting while a reload happens.

### Simulated users

- They act on the newest message they received. 10% of updates are delivered twice, concurrently.
- They act while waits are pending: bursts of earn presses, "Cancel" right after paying, "again" or a support reset right after picking a slot.
- They also wait out timers and ask support for a reset or to delete their data.

### Invariants (checked after the host drains)

- **I1 — one effect per logical action.** At most one irreversible effect per logical action and per cart. The oracle tag is the outbox row id.
- **I2 — one outcome per call.**
  - Exactly one outcome event per call that carries a token.
  - Exactly one accepted outcome per call, unless the wait was replaced or cleared.
  - No outcome accepted when there was no wait, and none ignored while the wait was pending.
- **I3 — no stuck users.**
  - The inbound queue and outbox are empty.
  - No sequential action is in the failure queue.
  - No snapshot still has a wait.
  - Nobody is on a waiting message without a wait.
- **I4 — sequential replay.** Each step is replayed with conditions recomputed on data where all earlier sequential actions have been applied, using the same document version. The final snapshot and data must match.
- **I5 — background actions never block.** Users whose background calls are slow must not wait at the barrier more often than other users, and no failed background action may stop a queue.
- **I6 — every reply validates.** Every reply passes the reference `checkReply`.
- **I7 — nothing lost.** No acknowledged update is lost, and the engine returned no errors.

## 2. Findings

### P1-1 — Memory grows without bound with large requests (embedded library and `figram-engine`)

**Claim.** `embedding.md` lines 209–233 describe memory, but only measure 2,000 small pizza requests. Lines 272–274 say documents can be reloaded "without memory growth". `ffi/figram_rt.c` sets the mimalloc arena reservation to zero (lines 22 and 33) for both the library and the executables.

**Evidence.**

| Run | Result | Log |
|---|---|---|
| Soak: 48 goroutines, waits growing and shrinking, reload every 300 ms | Memory 14 → 607 MB in 600 s, still 525 MB after every machine was unloaded and memory freed; Go heap about 60 MB, malloc under 2 MB; exit 1 | `LOGS/soak.log` |
| Same request (4,000 waits) repeated, no reloads, 24 threads | +1 MB/s, about 6 KB per request; threads, Go heap and malloc flat | `LOGS/memprobe-fixed.log` |
| Same, one goroutine | Same growth | `LOGS/memprobe-fixed-n4000-g1.log` |
| `figram-engine` process, growth per request | 0 at 0 and 100 waits; 2,050 B at 1,000; 6,184 B at 4,000; 22,061 B at 16,000 | `LOGS/repro-engine-rss-n*.log` (script `repro/engine_rss.py`) |
| With `MIMALLOC_ARENA_RESERVE=1GiB` | Flat in the engine process (`repro-engine-rss-n4000-arena1g.log`) and in the embedded library (`memprobe-fixed-arena1g.log`); 5-minute soak passes and falls back to 68 MB after stopping (`soak-arena1g.log`, exit 0) | as listed |

**Why it matters.** A host whose users hold hundreds of waits, or large snapshots, keeps growing until the Lean allocator kills the process with "INTERNAL PANIC: out of memory" (`embedding.md` 219–223).

**Proposed fix.** Don't default to a zero arena; or configure purging so freed OS memory is actually returned. Add a large-request case to `engine_memory.py` and to the C ABI tests, and qualify the "no memory growth" claim.

### P1-2 — The engine writes a snapshot it cannot read back under the request limit

**Claim.**
- `host-contract.md` 166–168 says snapshot growth is only a problem when calls go unanswered.
- `format.md` 579 promises the engine "never produces a snapshot it could not read back", but only in the context of `seq`.
- `host-contract.md` 322 classes `too_large` as "a bug in the host's request".

**Evidence.**
- `repro/limit_wedge.py` against `figram-engine` (`LOGS/repro-limit-wedge.log`; exit 1 means the problem reproduced):
  - A request carrying 29,124 waits (1,048,557 bytes) is accepted, and the reply snapshot has 29,125 waits.
  - The `call:award:ok`, `call:award:failed` and `restart` requests carrying that snapshot all get `too_large`.
- Through the C ABI and the reference wrapper: the near-limit section of `LOGS/bench.log` shows the wrapper returning `variant="error"` with a nil error.

**Why it matters.** A user can be wedged for good even by a host that answers every call, whenever calls come faster than outcomes (a slow external system, a user hammering a button). The only ways out are raising the limit, which is process-wide, or dropping the snapshot.

**Proposed fix.** Reject a step whose new snapshot exceeds the configured limit, with a dedicated error code. Or document the bound clearly (roughly 36 bytes per wait), how to detect it before commit, and how to recover.

### P2-1 — No importable Go wrapper for protocol 3

**Claim.** `embedding.md` 261–287 says "To use the wrapper in your own module…" and line 264 says it is a `main` package. Lines 250–259 say `ffl-go` v0.2.0 rejects protocol 3.

**Evidence.** `repro/import_gofig` (`LOGS/repro-import-gofig.log`, exit 1): `import "gofig" is a program, not an importable package`.

**Proposed fix.** Move the wrapper into an importable package with a separate test driver, or publish an `ffl-go` release for 0.3.0. Say plainly that today you have to copy the file.

### P2-2 — `Bot.Request` return contract is undocumented

**Claim.** `embedding.md` 268–271 says only that the reply is "validated strictly".

**Evidence.** `LOGS/abi-naive.log`: `need`, `bad_snapshot`, `bad_request` and `seq_overflow` all come back with `err == nil`, and only the variant string tells them apart. A host that only checks `err` stores the snapshot as `null`.

**Proposed fix.** Document the variants, or return typed errors for engine errors and a dedicated result type for `need`.

### P2-3 — Reload through the wrapper needs an undocumented whole-step retry

**Claim.** `embedding.md` 272–274: "Close… after which Request returns an error".

**Evidence.** `LOGS/abi-closed-window.log`: during reloads, the condition round trip hits `"bot is closed"` 23,794 times. Repeating the whole step on the current machine gave 0 wrong results. The error is an untyped `fmt.Errorf`, so the only way to recognise it is to compare strings.

**Proposed fix.** Export a sentinel error such as `ErrClosed`, and document the swap → close → retry-the-whole-step pattern.

### P2-4 — "Resume" or "back" can strand a user on a waiting message after its wait is gone

**Claim.**
- `host-contract.md` 165 advises drawing outcome arrows on a section or the root.
- `format.md` 149: variants with resume or back are not checked by the waiting-message warning.

**Evidence.** `repro/resume_into_wait.sh` (`LOGS/repro-resume-into-wait.log`):
- **booking bot:** leave `hold`; the outcome arrives on `lobby` and the root arrow releases the slot; then "book" resumes into `hold` with no wait.
- **Official `pizza.bot.json`:** /help from `placing`; the outcome arrives on `help` and is ignored; "back" returns to `placing` with no wait.
- Both files pass `figram check` cleanly.
- In the 2,001-user run this happened 6 times (users stood on `hold` with no wait until they gave up after 30 s). The two 3,000-user attempts that exceeded 15 minutes (`LOGS/green-big-*-timeout.log`) were probably slowed by the same thing, since users there waited 120 s each time; that part is a hypothesis.

**Proposed fix.** Add a `figram check` warning for a message that a variant with an outcome action leads to and that can also be reached by resume or back. Document the pattern; fix the pizza example.

### P3-1 — The reference validator rejects a documented shim reply

**Claim.** `embedding.md` 144–156 says shim errors (`bad_request` for a NULL or unloaded handle) carry `id: null`.

**Evidence.** `clients/go/main.go` line 404 only accepts a null id for `parse_error` and `too_large`. `LOGS/abi-shim-errors.log` shows `reply id <nil> instead of 1`.

**Proposed fix.** Accept a null id for shim-originated `bad_request` too.

### P3-2 — Reloading from `every` to latest-only drops all older waits

**Claim.** `host-contract.md` 372–375 says waits are kept across a reload.

**Evidence.** `repro/every_to_latest.sh` (`LOGS/repro-every-to-latest.log`): the next call of the action removes all three older waits at once, and their outcomes are then ignored.

**Proposed fix.** Document it in the reload section.

### P3-3 — Raw C ABI swap-and-unload while held crashes quickly

**Claim.** `embedding.md` 163–166 lists this as undefined behaviour. The shim's detection of unloaded handles invites the misreading that late requests are always caught. In `ffi/figram_ffi.c` 412–427, the window between reading the machine pointer and taking a reference covers the whole UTF-8 check of the request, so it grows with request size.

**Evidence.**
- `LOGS/abi-unload-raw-{1,2,3}.log`: SIGSEGV within 0.1–0.2 s every time.
- Same code with a per-handle reader count, waiting for readers before unloading: 0 wrong replies (`abi-unload-raw-wait.log`).
- The reference wrapper: 0 wrong replies; the longest `Close` took 250 ms (`abi-unload-wrapper.log`).

**Proposed fix.** Keep a reader count per slot in the shim, or show the wait-for-readers pattern for C hosts in the documentation.

### P3-4 — Performance numbers (`LOGS/bench.log`)

These fall in the class the private advisory PR-P3-11 already tracks; I report numbers only.
- Engine: about 63 ns per request byte, linear. 5 µs at 0 waits, 2 ms at 1,000, 16 ms at 8,000, 49 ms at 24,000.
- The wrapper adds about 1.6×. Writing the snapshot to SQLite adds a few percent on top.
- Sharing one machine across goroutines costs nothing measurable compared with one machine per goroutine.
- With the arena reserved, throughput with 16 threads was higher, but machine load during that run was 5, so treat it as indicative only.

## 3. Checked and found consistent

- **Controls**, `LOGS/control-*.log`, all exit 1:

  | Control | Result |
  |---|---|
  | No idempotency keys | I1, 132 duplicate effects |
  | No reconciliation | I1, a cart ordered twice |
  | No barrier (executor slowed by 3 ms) | I4: host saw `enough_points=false`, replay `true` |
  | Background actions inline | I5: 40.7% vs 2.9% |
  | Outcome recorded outside the transaction (crash in between) | I1, I2 (304), I3 (28) |
  | No per-user lock | I2: duplicate tokens |
  | Engine filter drops `pending` | I1, I2 (1,015), I3 |
  | Engine filter drops call token | I1, I3, I7 |
  | `detached: false` injected into replies | I6 and downstream |

  The same scenario on the real host passes (`LOGS/green-same-as-controls.log`). Validator controls: the reference corruptions above. Race detector canary: exit 66 (`LOGS/abi-race-canary.log`).
- **Crash matrix:** 13 single-boundary runs, all exit 0, with 7–184 crashes each (`LOGS/matrix-*.log`). One run at probability 0.2 starved slow background calls (`LOGS/matrix-reload_after_load-p0.2-starved.log`); all safety invariants still held there except I2 and I3 because of the starvation.
- **All boundaries plus reloads every 50 ms:** 178 crashes, 519 reloads, 140 recovered steps; passed (`LOGS/green-allcrash-reload.log`).
- **2,001 users:** 37,550 steps, 258 reloads, 958 recovered steps, 123 order outcomes on other messages, 407 ignored or replaced reserve outcomes, 230 reconciliation hits, 2,184 goroutines; passed in 140 s (`LOGS/green-big.log`).
- **Race-detector build:** 48 crashes, 202 reloads, 0 races (`LOGS/green-race.log`).
- **Engine semantics observed under load:**
  - Every accepted outcome removed its wait; repeated and stale outcomes were ignored.
  - `restart` and lost snapshots cleared waits; recovery kept them.
  - Timer arrows without a target consumed the timer; cancels and starts respected tokens.
  - `start` with an event and the high-water mark (101 cases) and run-id checks behaved as specified.
- **Wrapper concurrency:** `Bot.Request` is safe under concurrent use and `Close` really waits for running calls. Both bindings link into one binary, and the documented environment flags work.
- **Stack use:** 8 KiB regardless of the number of waits, from 0 up to 60,000 (`LOGS/repro-stackuse.log`), so the 128 KiB thread-stack advice holds for long arrays.
- **Design note:** a sequential call is listed before the waiting message's `send`, and the barrier holds the user's next event, so an outcome usually lands before the user reacts to "please wait". It arrives on another message only through double taps, old buttons or commands. My workload deliberately produced those cases.

## 4. Not done or limited

- **Stale-handle probe:** inconclusive, because the quarantine reuse order defeated my probe. Not reported.
- **Database rollbacks:** not modelled; SQLite never loses committed transactions here. Run ids are implemented and checked, but no rollback was injected.
- **Load figures:** latencies were measured on a shared 4-core machine at nice 10 (load average up to 5.5), so absolute host latency is indicative only.

Rerun commands: `scripts/{green,controls,matrix,rest}.sh` and `host/build.sh`; every finding's command is recorded in `LOGS/*.log`. The worktree `/home/user/figram-audit-worktrees/a6-hostgo` is clean.

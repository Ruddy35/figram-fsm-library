# Agent `protocol` (wave 4, implementation): clients, schemas, protocol tests for 0.3.0

Worktree `/home/user/figram-audit-worktrees/a4-protocol`, branch `audit4/protocol` (10 commits,
1dd16c4..ffba90c, merged into the release branch as cdc58df). Brief: `tools/BRIEF4.md`.
The harness did not let the agent write a report file; this is its final message, recorded
verbatim by the orchestrator. The orchestrator's handling of each point is in `../../FIXES.md`.

---

I finished protocol 3 for everything in my scope. Everything is committed on audit4/protocol in /home/user/figram-audit-worktrees/a4-protocol: 10 commits, 1dd16c4 to ffba90c, tree clean. All the checks below were run against the real engine at base 85371f5. The engine never disagreed with an expectation I wrote from the prose. I did not run the full ./verify.sh: the build and proof steps (1–5) and the C shim ASan/UBSan, reachability and Go -race driver steps (16, 18, 22) are your gate run. Logs are in /home/user/figram-audit/agents4/protocol/LOGS/.

**What changed**
- **Schemas:** `calls` values are a text or `{text, detached?, outcomes?, every?, x-…}`, a variant's `to` is optional, and snapshots allow `pending`.
- **Formats suite:** c_schema_table has 192 rows (was 118), and a_roundtrip, common.py and d_engine_requests know the new forms (96 rows, 20 new outcome rows). Each has new deliberate breaks, all red. `formats_final` exit 0.
- **Python client:** the validators accept `pending` and the new `call` fields and require protocol 3 in `hello`. New API: `Engine.outcome(key, snapshot, action, outcome, token, guards=None)`; bad arguments raise ValueError before anything is sent.
- **Go client:** the same validator changes. `gofig` now checks `hello` and replays the golden scenarios, including `call:` events with tokens. `fix_test` has 45 new rows and a `TestFixOutcomes` test.
- **tests/engine:**
  - protocol_edges: 250 cases (was 137), plus a `--json-only` mode.
  - snapshot_fixtures: 49 fixtures (was 30); an event with no arrow must return the snapshot unchanged.
  - harness_selftest: protocol_edges is now a tool, with 7 protocol-3 corruptions; 72 ok.
  - loadtest sends outcomes through `Engine.outcome`.
  - The client test doubles and engine_memory expect protocol 3.
- **host_sim:** rewritten.
  - `create_order` gets exactly one outcome event with its token, recorded in the same transaction that marks it executed: `created`; `rejected` with a reason, which `address_rejected` reads; or `failed` after 3 attempts.
  - `notify_kitchen` runs from a separate background queue. When the kitchen is down for good, it goes to the failure queue with an alert and the user is not held up.
- **C ABI:** `contract protocol3` is a new subtest, stress has 8 protocol-3 replies compared byte for byte, and pizza's message count (13) is fixed. regress.sh passes 46/46 and the shim mutants are all caught.
- **Gates and docs:**
  - verify.sh, verify-prebuilt.sh, docs/testing.md and the verify-faults comments have the raised floors and the new host and Go checks.
  - ci/check_docs.py has a new `protocol` check that the version is the same everywhere it is stated.
  - verify-prebuilt.sh passes end to end, and check_docs reports 0 problems.

**Negative controls (all red)**
- Python: the base-commit protocol.py; validators that accept `detached:false`, any `hello` version, or a wait without a token.
- Client: a swapped outcome event, a missing token, unchecked arguments, a client that accepts only protocol 2.
- Snapshots: the old snapshot schema; dropping `pending` from the expected snapshot form in the echo check.
- host_sim: outcome recorded separately (users stuck on `placing`); background actions run in the user's queue (users blocked). The no-barrier host is also still red.
- Go: base-commit main.go (8 rows fail, golden replay fails); lax validators (10 rows fail); 4 new corruptions.
- C: a link-time wrapper that drops `detached` fails both `protocol3` and stress.
- check_docs: on its first run the new check caught ffi/figram.h still saying "version 2" (I fixed that comment).

**Things you should look at**
1. **I changed the scenario size in the gates from 200×40 to 100×120.** With the new examples, 200×40 covers only 48/53 pizza and 74/75 features variants, and 6 mutations survive, so steps 7 and 9 of verify.sh would fail. 100×120 covers 53/53 and 75/75 and catches 166/166 mutations. Mutation testing takes about 100 s instead of about 71 s. If you would rather bias the scenario generator in Trace.lean, revert this.
2. **host-contract.md contradicts itself on timer marks.** Section 1 step 2 says keep the mark until `seq` passes the token. Section 3 says keep it while the snapshot still holds the timer, because an arrow without a target raises `seq` but leaves the timer. The engine agrees with section 3. Repro on features:
   - `step {"at":"home","timers":{"idle":1},"seq":1} btn:log` gives seq 2 with idle:1 kept.
   - `step {"at":"home","timers":{"idle":1},"seq":2} timer:idle token 1` is accepted again.
3. **Minor:** two identical waits `[{ping,3},{ping,3}]` are both removed by one outcome, which doesn't match "each wait accepts one outcome". The engine never writes such a snapshot.
4. **Not touched:** docs/embedding.md:254 still says "v0.2.0 bundles this release (protocol 2)", and CHANGELOG has nothing for 0.3.0 yet. Both are yours.

**Files outside my list, each in its own commit so you can drop them**
- docs/format.md and README.md: doctest outputs updated to protocol 3, 13 messages and the vip timer; README also gets an `Engine.outcome` example (f151bbe, ffba90c).
- docs/host-contract.md section 7 (with c515a1c): now describes the new host_sim.
- ci/test-bundle.sh (a8d7ae2): expects `"protocol":3`.
- ffi/figram.h (with b2c2a2a): the header comment now says version 3.

**Not done:** json_fuzz.py is unchanged (it only tests the JSON text layer, so nothing to add), and I did not run the full `go test ./...`, the TSan builds, or tests/compiler.

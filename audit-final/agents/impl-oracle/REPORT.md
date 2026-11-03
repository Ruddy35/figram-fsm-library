# Agent `oracle` (wave 4, implementation): independent oracle and compiler tests for 0.3.0

Worktree `/home/user/figram-audit-worktrees/a4-oracle`, branch `audit4/oracle` (commit d4db4d2,
merged into the release branch as 3c28c72). Brief: `tools/BRIEF4.md`. The agent did not read
`Figram/Core.lean`, `Chart.lean`, `Compile.lean` or `Engine.lean`. The harness did not let the agent
write a report file; this is its final message, recorded verbatim by the orchestrator. The
orchestrator's handling of each point is in `../../FIXES.md`.

---

**oracle (a4-oracle): the engine matches the prose everywhere the prose is clear. I found no clear disagreement between the engine and the prose.** Where the prose is ambiguous, the engine's choices are written down below with reproductions.

Commit `d4db4d2` on branch `audit4/oracle`. I changed only `tests/compiler/*` and `docs/testing.md`. I did not read `Figram/Core|Chart|Compile|Engine.lean`. The working tree is clean.

**What changed**
- **`oracle.py`**: now covers background actions, actions with outcomes and arrows without a target, all from the prose. That includes `detached`/`token` on `call`, `pending` newest first, latest vs `every`, the token check, the wait removed even without an arrow, bubbling, restart clearing waits, recovery keeping them, `start` rejecting outcome events, and the declaration checks V3–V6. Each ambiguity is a switch (A8–A15), defaults set to the engine's reading, with hits counted.
- **`gen.py`**: random documents with background actions, outcome actions (latest/`every`, one name containing `:`) and outcome arrows on messages, sections and the root. Some variants have no `to`; documents stay valid under V3–V6. Upgrades now remove an action, drop its outcomes or toggle `every`. Snapshots are perturbed with forged, duplicate and foreign waits.
- **Driver** (`diff_random.py`, `difflib_core.py`, `exhaustive.py`): sends outcomes with the right, stale, forged and undeclared tokens or names, and covers every branch at the initial message (start with an event, recovery). Every reply is compared byte for byte; only the key order inside `resume`/`timers` is ignored, because the prose doesn't fix it (the engine sorts them). A document the oracle rejects must also be rejected by the engine.
- **`directed.py`**: 39 new hand-written cases, one or more per rule you listed (81 conformance cases in total).
- **Other files**: `metamorphic.py` renames `call:` events, outcomes and `pending`, and gains `call_forms` and `all_detached`. `check_diff.py` has 30 new acceptance kinds. `lint_warnings.py` has 11 cases for "outcome … has no arrow on …" and `lint_gaps.py` a new case. `mutants_red.py` and `shrink.py` are adapted. New `filter_bin.py` wraps the real engine or `figram` and damages one thing in its output. `run_all.sh` has the new red runs.
- **Mutant cap**: `mutants_red.py` now runs at most 100 sampled mutants of each kind per document. Without the cap the run exceeded the 840 s timeout: outcome arrows on the root add rows to every message, and one document offered 2,107 mutants. With the cap: 2,350 of 4,657 offered, about 400 s.

**Checks (all exit 0; logs in `/home/user/figram-audit/agents4/oracle/LOGS/`)**
- `diff-random` (600 docs, 120 steps, seed 1, run_all options): 0 divergences, 708,631 requests, 684,670 compared byte for byte.
- `diff-random-s1` … `s6` (40 docs, 60 steps): all 0.
- Also green: `designer-same-version`, `designer-upgrades`, `directed-conformance` (162 runs), `directed-findings`, `directed-designer-oracle`, `metamorphic` (10 must-hold transforms, 0 violations, 740 outcome events), `check-diff` (1,140 cases, 0 disagreements), `lint-warnings` (27), `lint-gaps` (8), `mutants` (0 surviving mutants that aren't equivalent).
- `ci/check_docs.py` (after `bash ffi/build.sh`) exits 1 with 5 problems, all from the unfinished WIP docs: doctests still expect protocol 2 and 11 states, and `docs/testing.md` still says 26 golden scenarios where the sources have 37. None is in text I changed.

**Negative controls (each red as required; logs `red-*`, `alt-reading-*`)**
- 16 new oracle bugs: red in `diff_random` and in `directed`.
- The 12 existing oracle bugs are still red.
- 8 engine defects via `filter_bin.py` are red. Two of them (`empty-pending`, `pending-after-seq`) are caught only by the byte comparison.
- Metamorphic `--break-map pending` and `--break-map outcome-event` are red.
- `lint_warnings` is red when the outcome warning is filtered out.
- Each alternative reading A8–A15 goes red against the engine, which confirms the defaults are the engine's readings.

**Ambiguities in the prose (engine reading = oracle default; reproductions in `/home/user/figram-audit/agents4/oracle/REPRODUCTIONS/`)**
- **A11 / A15** (`a11-duplicate-wait.sh`): the engine treats `pending` as a set of (action, token).
  - One accepted outcome removes every identical copy of a wait. The prose says "each wait accepts one outcome", which would remove only one.
  - A new `every` wait replaces an identical forged one.
  - Both only happen with forged snapshots.
- **A13** (`a13-empty-outcomes.sh`): `"outcomes": []` is accepted as "no outcomes", so no token and no V6. `[]` together with `"every": true` is rejected. Decision 11 doesn't list `[]` among the explicit defaults.
- **A8**: leaving a message also sends `cancel_timer` for a timer that an arrow without a target already consumed. This matches the literal decision 2, but isn't said explicitly.
- **Minor, for whoever owns the protocol**: a `call:` event that isn't a well-formed outcome event (e.g. `call:x`) is `bad_request` even without a token. The error table's "invalid `event`" covers it, but it isn't spelled out.
- **Matching the literal prose**:
  - A9: two waits from one step are listed with the later call first.
  - A10: a new call replaces all older waits of a non-`every` action.
  - A12: an undeclared outcome name is accepted.
  - A14: in recovery, an accepted outcome with no arrow removes its wait and gives the plain start over.

**Not done**
- I didn't run the full `run_all.sh` or `verify.sh`. I ran each `run_all.sh` step separately, with the same arguments.
- No floors in `verify.sh` / `verify-prebuilt.sh` check the compiler suite, so nothing there to raise. I changed the duration in `docs/testing.md` from "about 8 minutes" to about 10.
- I didn't fix the WIP doctests, which belong to you.

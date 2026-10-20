# Figram audit — shared brief for every subagent

Read this whole file before you start. It is the contract between you and the orchestrator.

## Baseline

* Main repo (do NOT modify, do NOT commit to it directly): `/home/user/figram-fsm-library`
* Baseline branch: `audit/baseline` = commit `d69bbaf7881a0ae1c11b75a35cf5193ad604b925`
  (byte-identical import of the user's archive `figram-lean_4.zip`,
  SHA-256 `7769c0030bb2c516060015039263764d778ead91a92d8b2213f4fa1d9f8d1329`).
* Project: Lean 4 FSM engine for Telegram bots. Docs: README.md, FORMAT.md, HOST.md, AUDIT.md,
  REVIEW.md. Sources: `Figram/*.lean`, `Main.lean`, `EngineMain.lean`, `ffi/`.
* Machine: 4 cores, 16 GB RAM, Ubuntu 24.04, gcc 13, clang 18, Go 1.24.7, Python 3.13, jsonschema 4.26.
  No internet except package mirrors / git reads; GitHub release downloads are blocked.

## Your worktree (mandatory)

The orchestrator has already created yours: `/home/user/figram-audit-worktrees/<NAME>` on branch
`audit/<NAME>` (at baseline). Work only there (`cd` into it). If you need an extra one:
`git -C /home/user/figram-fsm-library worktree add -b audit/<NAME>-2 /home/user/figram-audit-worktrees/<NAME>-2 audit/baseline`. Never touch other
worktrees, never `reset --hard`/`clean -fdx` anything you did not create, never push.
Commit your own tests/fixes in your branch (author is preconfigured). Commit message must end with:

```
Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01JgWRtX2jyUidN8gxEhe5qx
```

## Toolchain (Lean 4.35.0 built from source)

GitHub release binaries are unreachable, so the orchestrator builds Lean v4.35.0
(tag commit eb6cafb7) from source with gcc/libstdc++ (official releases use clang/libc++).
When ready, the file `/home/user/figram-audit/TOOLCHAIN_READY` exists and:

* `source /home/user/figram-audit/env.sh` puts `lean`, `lake`, `leanc` on PATH;
* `/home/user/figram-audit/fresh/` holds FRESHLY BUILT baseline artifacts:
  `figram`, `figram-engine` (unstripped), `ffi-out/` (libfigram.a + libs for linking, see
  `fresh/README.txt`), plus `build.log`.
* To build your own worktree fast: `cp -a /home/user/figram-audit/fresh/lake-cache <worktree>/.lake`
  then `lake build` (only changed modules rebuild).

Until the marker exists: read code, write tests, and you MAY run the archive's prebuilt
`bin/figram`, `bin/figram-engine` for exploration only — label every such result `PREBUILT`.
Anything you report as CONFIRMED must be re-run on the fresh build (or on your own build of
your worktree) and say which binary was used. To wait for the marker use a background
`until [ -f /home/user/figram-audit/TOOLCHAIN_READY ]; do sleep 20; done` (Bash run_in_background).

### UPDATE 22:10Z — bin/* is CI-verified

GitHub Actions run 39330382243 (official Lean 4.35.0 via elan, ubuntu-latest, 4 cores) rebuilt the
baseline from source and `verify.sh` printed `ok: хеши совпадают побайтно`: stripped fresh binaries
are byte-identical to the archive's `bin/figram`, `bin/figram-engine`. Log:
`/home/user/figram-audit/LOGS/10-ci-verify-run-39330382243.log`.
So results obtained with `bin/figram*` of the baseline count as results on the official fresh build —
label them `bin (CI-verified = official fresh build)` and cite that log. You still need the local
toolchain for: anything with modified sources (fixes, mutants, regression tests of Lean code),
`libfigram.a` / C / Go (the archive has no prebuilt library), and `#print axioms`.

## Resource rules

* CPU is shared by ~8 agents and a compiler build. Wrap heavy commands:
  `nice -n 10 timeout <secs> prlimit --as=4000000000 -- <cmd>` (adjust). No job longer than
  15 min. At most one heavy job at a time per agent. Before TOOLCHAIN_READY: only short runs.
* Hostile-input experiments only locally, with limits. No real Telegram/CRM/payment/network calls.

## Output (mandatory)

Write everything under `/home/user/figram-audit/agents/<NAME>/`:

* `REPORT.md` — sections: Scope & contract; Guarantee table
  (`guarantee | test | oracle | actually run? | coverage | gap`); Findings; What I could not check.
* `LOGS/` — every command you rely on: command line, exit code, stdout/stderr (use
  `/home/user/figram-audit/run.sh <logname> <cmd...>` which records all of that).
* `REPRODUCTIONS/` — minimal standalone repro scripts/inputs (each runnable with one command).
* Tests and fixes: commit in your branch; list commit hashes in REPORT.md.

Each finding (one row/section each, deduplicate by root cause):

`ID | module | class (PRODUCT / INTEGRATION / EXAMPLE / TEST / DOC / BUILD) | reachable production path |
trigger/preconditions | impact | likelihood (qualitative, why) | Risk 0–10 | P0–P3 | CONFIRMED / LIKELY / HYPOTHESIS |
baseline commit | repro command | expected / actual | exit code | log path | proposed fix | regression test | fix commit | retest`

Classification (strict):
* PRODUCT = Lean core/compiler/format/engine code and the `figram`, `figram-engine` binaries,
  `ffi/figram_ffi.c` + `Figram/Api.lean` as the shipped embedding ABI, FORMAT/HOST contracts.
* INTEGRATION = code a user would copy to integrate (Python client `figram_client.py`, Go `Bot` wrapper)
  — report risk "if copied", not as a core defect.
* EXAMPLE = demo code (`go-example` main/test harness, `host_sim.py`).
* TEST = test harness, verify scripts, CI workflows. DOC = documentation claims. BUILD = build scripts.

Severity: P0 data loss/corruption of committed data, duplicate irreversible action, RCE, user isolation
break on the normal production path. P1 realistic availability failure/deadlock/DoS reachable by
external input, significant protocol error. P2 rare branch, controlled failure, bounded resource risk.
P3 cosmetic/doc/example/flaky CI. Score product risk only for reachable paths (example-only bug → N/A
in product table).

Rules: never claim "fixed/safe/proved/passed" without an attached command + exit code + log of a real
run. Every important guarantee needs a negative check that you saw turn red. Don't accept exit 0 on
zero cases/comparisons. Fix only after a failing regression test exists: show FAIL before, PASS after.
Do not say "all bugs found"; state coverage and residual risk.

NOTE: the harness does not let subagents create `.md` report files. Do not work around it. Put the
FULL REPORT.md text (Russian, all sections) at the end of your final message under a line
`=== REPORT.md ===`; the orchestrator saves it. LOGS/, REPRODUCTIONS/, scripts and commits are fine.

Final message to the orchestrator: ≤ 60 lines — path to REPORT.md, findings table (ID, class, P,
confidence, one-line summary), commits, what is BLOCKED/NOT RUN.

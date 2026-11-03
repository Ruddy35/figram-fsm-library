# Figram 0.3.0 — implementation brief for the test, client and tooling agents

Read this whole file first. It is the contract between you and the orchestrator.

## What is being built

Figram 0.3.0 adds three features to the bot format and the engine protocol (version 3):

* **Background actions** — an action declared `"detached": true`; its `call` carries `"detached": true`.
* **Actions with outcomes** — an action declared `"outcomes": [...]` (optionally `"every": true`).
  The step that performs it gives it a token (the step's `seq`) and records a wait in the
  snapshot (`"pending": [{"call": …, "token": …}]`). The host sends back exactly one event
  `call:<action>:<outcome>` with that token (a declared outcome, or `failed`). The engine accepts
  it only with the token of a wait, removes the wait, and then handles it like any event (arrows,
  bubbling); an accepted outcome without an arrow is ignored but its wait is gone. Moves keep
  the waits; a new call of the same action replaces its wait unless `every`. `restart` clears
  the waits; recovery keeps them. `start` rejects an outcome event.
* **Arrows without a target** — a variant without `to`: its actions, no move (no exit, no entry,
  timers and places unchanged), `seq` + 1; a timer firing handled this way is consumed (removed
  from `timers`). In recovery / `start` with an event such a variant leads to the initial message.

The specification is the prose, and it is authoritative:

* `docs/format.md` — section 3 (rows 6, 9, 11, 13 of the derivation table, V3–V6, warnings),
  section 4 (grammar: `Call`, optional `to`, the reserved prefix `call:`), section 7 (decisions
  11, 12, 13 — read them together with 1–10), section 8 (protocol 3: `pending`, the outcome
  request row, rule 2, the `call` action fields, `restart`);
* `docs/host-contract.md` — section 1 step 3 (the barrier excludes background actions), section 2
  (background actions, outcomes, failures), section 3 (the timer mark rule), section 4, section 6.

The Lean engine already implements it on the base commit `85371f5` of your branch (the doctests
and some docs examples are not updated yet; `examples/*.bot.json` and `examples/golden.jsonl` are
regenerated and use the new features). `.lake` is copied; build with:

```sh
source /home/user/figram-audit/env.sh
export LEAN_CC=/home/user/figram-audit/fixes/memory/cc-quiet.sh
nice -n 10 timeout 1500 lake build      # figram, figram-engine in .lake/build/bin
bash ffi/build.sh                       # libfigram.a for C/Go (ffi/out)
```

## Rules

* Work only in your worktree `/home/user/figram-audit-worktrees/a4-<NAME>` (branch `audit4/<NAME>`).
  Never touch other worktrees or the main repository, never push, never create tags, never
  `reset --hard`/`clean -fdx` what you did not create.
* **Do not edit `Figram/*.lean`, `Main.lean`, `EngineMain.lean` or `tests/proofs/`** — the
  orchestrator owns the Lean sources. If you find a disagreement between the engine and the prose,
  write a minimal reproduction (request lines + expected/actual per the prose) and report it; do
  not "fix" the test to match the engine unless the prose agrees with the engine.
* Commit your work in your branch with messages ending with:

```
Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01JgWRtX2jyUidN8gxEhe5qx
```

* 4 cores and ~7 GB disk are shared by 2 agents and the orchestrator's builds. Wrap heavy Python
  runs with `nice -n 10 timeout <secs>`; at most one heavy job at a time; no job over 15 min. Do NOT
  run the full `./verify.sh` or `tests/compiler/run_all.sh` (the orchestrator runs the gate).
* Record commands you rely on with `/home/user/figram-audit/run.sh <logpath> <cmd...>` into
  `/home/user/figram-audit/agents4/<NAME>/LOGS/`.
* Every new check must be shown to go red on a deliberate defect (a negative control) and green
  on the real engine. Never accept exit 0 on zero cases. Raise the counted floors in `verify.sh` /
  `verify-prebuilt.sh` / `docs/testing.md` when you add cases, and keep `ci/check_docs.py` green
  (it needs `bash ffi/build.sh` first).

## Output

Final message: a summary of at most 40 lines (what you changed, commits, which checks ran with
exit codes and log paths, negative controls, disagreements found between prose and engine with
reproductions, what is NOT done). Russian or English.

# Figram 0.3.0 — final audit brief

Read this whole file first. It is the contract between you and the orchestrator.

## What is audited

Figram 0.3.0 (unreleased) adds to the bot format and the engine protocol (version 3):

* **background actions** — an action declared `"detached": true`; its `call` carries
  `"detached": true`;
* **actions with outcomes** — an action declared `"outcomes": [...]` (optionally `"every": true`);
  the step that performs it gives it the token of the step and records a wait in the snapshot
  (`"pending"`); the host sends back one event `call:<action>:<outcome>` (a declared outcome or
  `failed`) with that token; the engine accepts it only with the token of a wait, removes the
  wait and handles it like any event; moves keep the waits; a new call replaces the wait unless
  `every`; `restart` clears the waits, recovery keeps them; `start` rejects outcome events;
* **arrows without a target** — a variant without `to`: its actions without a move; a timer
  firing handled this way is consumed; on entering the bot it leads to the initial message.

The specification is the prose, and it is authoritative:

* `docs/format.md` — sections 3 (derivation rows 6, 9, 11, 13; V1–V6; warnings), 4 (grammar),
  7 (decisions 1–13; read 11–13 together with 1–10), 8 (protocol 3);
* `docs/host-contract.md` — sections 1–6;
* `docs/proofs.md` — what is claimed to be proved; `docs/testing.md` — what is claimed tested.

The implementation is on the base commit of your branch: Lean sources `Figram/*.lean`
(`Core.lean`, `Chart.lean`, `Compile.lean`, `Engine.lean`, `Format.lean`; proofs in
`CoreProofs.lean`, `Correct.lean`, `EngineProofs.lean`, `FormatProofs.lean`, `OutcomeProofs.lean`),
pinned theorems `tests/proofs/THEOREMS.expected`, `tests/proofs/Statements.lean`, clients
`clients/python`, `clients/go`, schemas `schemas/`, tests `tests/`. `.lake` is copied; build:

```sh
source /home/user/figram-audit/env.sh
export LEAN_CC=/home/user/figram-audit/fixes/memory/cc-quiet.sh
nice -n 10 timeout 1500 lake build      # figram, figram-engine in .lake/build/bin
bash ffi/build.sh                       # libfigram.a for C/Go (ffi/out)
```

## Rules

* Work only in your worktree `/home/user/figram-audit-worktrees/a5-<NAME>` (branch `audit5/<NAME>`).
  Never touch other worktrees or the main repository, never push, never create tags, never
  `reset --hard`/`clean -fdx` what you did not create.
* This is an **audit**: do not change product code (`Figram/*.lean`, `Main.lean`,
  `EngineMain.lean`, `clients/`, `schemas/`, `ffi/`). You may add files under
  `/home/user/figram-audit/agents5/<NAME>/` (reports, reproductions, scratch Lean files, scripts).
  Lean experiments go into scratch files run with `lake env lean <file>`; mutations of the
  sources for red-team checks are made in a copy or reverted with `git checkout -- <file>` before
  you finish (`git status` must be clean at the end, apart from untracked scratch you list).
* Hostile inputs only locally and bounded (`timeout`, `prlimit`); no network services, no real
  Telegram, CRM or payment systems. **Never construct hash-collision keys or attack material
  against hash tables**; report such a class of issue in words only.
* 4 cores and ~7 GB disk are shared by up to 4 agents. Wrap heavy runs with
  `nice -n 10 timeout <secs>`; at most one heavy job at a time per agent; no job over 15 min. Do
  NOT run the full `./verify.sh` (the orchestrator runs the gate).
* Record the commands your findings rely on with
  `/home/user/figram-audit/run.sh <logpath> <cmd...>` into
  `/home/user/figram-audit/agents5/<NAME>/LOGS/`. A finding without a command, an exit code and
  a log of a real run is a hypothesis: label it so.
* Do not claim "no bugs": say what you checked and how.

## Output

Write `/home/user/figram-audit/agents5/<NAME>/REPORT.md`: findings ranked P0 (wrong behaviour or
a false claim of proof/safety), P1 (a gap a user or host can hit, a missing theorem for a claimed
property, a test that cannot fail), P2 (docs/consistency), P3 (nits). Each finding: the claim
(file:line of the prose or the code), the evidence (command, exit code, log path, minimal
reproduction), why it matters, a proposed fix. Then a section "Checked and found consistent"
listing what you verified. Final message: a summary of at most 40 lines with the findings and
the report path.

# Figram 0.3.0 — second audit round brief

Read this whole file first. It is the contract between you and the orchestrator.

## What is audited

Figram 0.3.0 (unreleased) at commit 598fec6 of branch `claude/kind-ramanujan-n4q8mg`: background
actions (`detached`), actions with outcomes (`outcomes`, `every`, implicit `failed`, outcome events
`call:<action>:<outcome>` with the token of the call, waits in the snapshot field `pending`),
arrows without a target, protocol 3. A first audit round (formalization, semantics against the
prose, protocol/host contract, red team) already ran; its findings were fixed in 7239ab7..598fec6.
Its archive is the branch `audit/2026-11-03` (`audit-final/`: EXECUTIVE.md, FIXES.md, agents'
reports) — read `audit-final/FIXES.md` there (`git -C <your worktree> show
audit/2026-11-03:audit-final/FIXES.md`) to know what was found and changed.

The specification is the prose, and it is authoritative: `docs/format.md` (sections 3, 4, 7, 8),
`docs/host-contract.md` (sections 1–7), `docs/embedding.md` (C ABI, Go), `docs/proofs.md`,
`docs/testing.md`. The October audit before 0.1.0 had a wave of independent production-like hosts;
its host specification, bots and corpora are in the public history:
`git show 16c885a:audit-final/hosts/README.md` (and the files next to it) — a reference for the
style of the work, not code to reuse.

Build (`.lake` is copied; `lake build` is a no-op):

```sh
source /home/user/figram-audit/env.sh
export LEAN_CC=/home/user/figram-audit/fixes/memory/cc-quiet.sh
nice -n 10 timeout 1500 lake build      # figram, figram-engine in .lake/build/bin
bash ffi/build.sh                       # libfigram.a for C/Go (ffi/out); already built in a6-hostgo
```

## Rules

* Work only in your worktree `/home/user/figram-audit-worktrees/a6-<NAME>` (branch `audit6/<NAME>`)
  and your output directory `/home/user/figram-audit/agents6/<NAME>/`. Never touch other worktrees
  or the main repository, never push, never create tags, never `reset --hard`/`clean -fdx` what you
  did not create.
* This is an **audit**: do not change product code (`Figram/*.lean`, `Main.lean`,
  `EngineMain.lean`, `clients/`, `schemas/`, `ffi/`, `docs/`, the verify scripts). Your own code
  (hosts, simulators, scripts, reproductions) goes into your output directory. `git status` of your
  worktree must be clean at the end, apart from untracked build output.
* No real Telegram, CRM, payment systems or any network service: everything external is simulated
  locally. Hostile inputs only locally and bounded (`timeout`, `prlimit`). **Never construct
  hash-collision keys or attack material against hash tables**; report such a class in words only.
* 4 cores and ~6 GB disk are shared by 3 agents. Wrap heavy runs with `nice -n 10 timeout <secs>`;
  at most one heavy job at a time per agent; no job over 15 min; keep your output under 300 MB.
* Record every command a finding relies on with `/home/user/figram-audit/run.sh <logpath> <cmd...>`
  into `/home/user/figram-audit/agents6/<NAME>/LOGS/`. A finding without a command, an exit code
  and a log of a real run is a hypothesis: label it so.
* Every check you build must be shown to go red on a deliberate defect (a negative control: a
  broken host, a broken engine via a filter, a wrong expectation) and green on the real code.
  Never accept success on zero cases.
* Do not claim "no bugs": say what you checked and how.

## Output

Write `/home/user/figram-audit/agents6/<NAME>/REPORT.md` if you can (if the harness refuses report
files, put the whole report into your final message instead): findings ranked P0 (wrong
behaviour, data loss, a duplicated irreversible action, a false claim), P1 (a gap a host following
the documents can fall into, a missing check for a claimed property), P2 (docs/consistency), P3
(nits). Each finding: the claim (file:line of the prose or the code), the evidence (command, exit
code, log path, minimal reproduction), why it matters, a proposed fix. Then "Checked and found
consistent". Final message: a summary of at most 40 lines with the findings and the report path.

# ffl-go 0.3.0 — targeted audit brief

Read this whole file first. It is the contract between you and the orchestrator.

## What is audited

The Go module `github.com/Ruddy35/ffl-go` at commit **51c2312** (PR Ruddy35/ffl-go#3, not merged):
the update to Figram v0.3.0 and its engine protocol **version 3**. The module wraps the static C
library of the Figram release (`lib/linux_amd64`) and offers a typed API (`protocol.go`,
`figram.go`).

New in this commit:
- `Bot.Outcome(snapshot, action, outcome, token, guards)`, which sends the event
  `call:<action>:<outcome>` with the token of the call;
- `Snapshot.Pending []Wait{Call, Token}`;
- `Action.Outcomes`, `Action.Token` and `Action.Detached` for `call`;
- `CodeSnapshotTooLarge`;
- the reply validator `checkReply`, which now accepts `pending` and `token`/`detached` in calls;
- docs (README, doc.go, CHANGELOG) and tests.

The spec is the prose of Figram v0.3.0, which is authoritative: `docs/format.md` (sections 7 and 8;
decisions 11, 12 and 13; errors), `docs/host-contract.md` (sections 1–6) and `docs/embedding.md` (Go).
These live in the Figram worktree `/home/user/figram-audit-worktrees/cleanup-int`, which is
**read-only** for you; its tree equals master and the published v0.3.0 apart from docs. Reference
validators:
- Python: `clients/python/protocol.py` (`check_reply`, `check_snapshot`, `check_actions`);
- Go: `clients/go/main.go` (`checkReply`).

Engine binaries: `/home/user/figram-audit-worktrees/cleanup-int/.lake/build/bin/figram` and
`figram-engine`. Do not rebuild them and do not run `lake` there. `figram trace <doc> <count>
<len> <seed> --directed` gives reference scenarios, and `figram random <seed> <messages>` gives
random valid documents.

## Questions, in priority order

1. **Differential validator.** Does ffl-go's `checkReply` accept exactly the replies the reference
   validators accept, for the requests ffl-go sends?
   - Generate thousands of replies:
     - valid ones, from engine runs on pizza, features and random documents, covering outcomes,
       pending, detached, need, recovered, errors and hello;
     - mutated ones: drop, add or retype fields, wrong ids, unknown codes, `detached:false`, token
       as string or float, pending entries with extra keys, and so on.
   - Compare the verdicts of ffl-go with Python `check_reply` and Go `clients/go` `checkReply`.
   - Classify each difference as a bug, an intended difference, or a gap in the reference. Known
     intended differences: ffl-go accepts no `mutant` in hello and has no error reply without
     `code`.
2. **Typed round trip.** For every valid engine reply, does converting it to ffl-go's types and
   marshalling the types back give the engine's JSON exactly (field order, presence of `token` and
   `detached`, `pending` order)?
   - Run this over many traces, including `every` waits and calls whose actions have `:` in their
     names.
   - A lost or reordered field is a finding.
3. **Host scenarios through ffl-go.** Write a small Go host on the module:
   - many goroutines and users;
   - actions with outcomes (latest-only and `every`), background actions and timers;
   - repeated and stale outcomes;
   - `restart`;
   - a document reload while requests are running (`Close`, `ErrClosed`, retry of the whole step);
   - `snapshot_too_large` under `SetMaxRequest`.

   Compare every step with `figram-engine` given the same request (send the same JSON line to the
   process). Report any difference, panic, race (`-race`) or deadlock.
4. **API and documentation review.** Check `README.md`, `doc.go`, `CHANGELOG.md` and the comments in
   `protocol.go` against the prose:
   - semantics of `Outcome`: action names containing `:`, the outcome `failed`, token 0, and an
     outcome for an action without outcomes;
   - what `Outcomes`/`Token` mean on a `Call` without a token;
   - whether the example code is correct;
   - anything a Go developer would get wrong by following these docs.
5. **Tests of the module.** Are there claimed properties without a test? Each mutant of
   `scripts/mutants.py` is caught; propose missing mutants and show that they survive, if any do.

## Rules

* Work only in your worktree `/home/user/figram-audit-worktrees/a7-fflgo` (branch `audit7/fflgo`)
  and your output directory `/home/user/figram-audit/agents7/fflgo/`.
  - Never touch other worktrees, `/home/user/ffl-go` or the Figram repository.
  - Never push or create tags, and never `reset --hard` or `clean -fdx`.
* This is an **audit**: do not change the module's code (`*.go`, `lib/`, `scripts/`, `testdata/`,
  docs). Your own code (hosts, generators, scripts) goes into your output directory. To test a
  defect, copy the module into the output directory and change the copy. `git status` of your
  worktree must be clean at the end.
* No network services. Run hostile inputs only locally and bounded (`timeout`, `prlimit`). **Never
  construct hash-collision keys or attack material against hash tables.**
* Resources: 4 cores and limited disk, shared with other work.
  - Wrap heavy runs with `nice -n 10 timeout <secs>`.
  - Run at most one heavy job at a time, and no job longer than 15 minutes.
  - Keep your output under 200 MB.
  - Set up the toolchain with `source /home/user/figram-audit/env.sh` (Go 1.24, Python 3.13).
* Record every command that a finding relies on with
  `/home/user/figram-audit/run.sh <logpath> <cmd...>` into `/home/user/figram-audit/agents7/fflgo/LOGS/`.
  A finding without a command, an exit code and a log of a real run is a hypothesis: label it so.
* Show that every check you build goes red on a deliberate defect: a broken copy of ffl-go, a
  wrong expectation, or a corrupted reply. Show that it goes green on the real code. Never accept
  success on zero cases.
* Do not claim "no bugs"; say what you checked and how. Budget: about 60–90 minutes.

## Output

Your final message is the report, because the harness may refuse report files. Rank the findings:
- **P0**: wrong behaviour, data loss, or a false claim in the docs.
- **P1**: a gap a Go host following the docs can fall into, or a missing check for a claimed property.
- **P2**: docs or consistency.
- **P3**: nits.

For each finding give: the claim (file:line), the evidence (command, exit code, log path, minimal
reproduction), why it matters, and a proposed fix. Then add a section "Checked and found
consistent" with numbers. Start with a summary of at most 30 lines.

# Changelog

All notable changes to this project are documented in this file. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Clients rely on the document and
snapshot formats, the engine protocol (operations, JSON field names, error `code` values), the CLI
command names and the exit statuses; the texts of messages are for humans and may change.

## [Unreleased]

### Added

- Background actions: an action declared in `calls` with `"detached": true`
  (`"calls": {"notify_kitchen": {"text": "…", "detached": true}}`). Its `call` carries
  `"detached": true`; the host may run it later and in any order, and the user's next event does
  not wait for it ([docs/format.md](docs/format.md), section 7, decision 11;
  [docs/host-contract.md](docs/host-contract.md), sections 1 and 2). An unavailable notification
  service no longer blocks the dialogue.
- Actions with outcomes: an action declared with `"outcomes": ["created", "rejected"]` reports how
  it ended as the event `call:<action>:<outcome>` with the token of its call; every such action
  also has the outcome `failed` (no answer, an error, retries exhausted). The step that performs
  it gives it a token and records a wait in the snapshot (`"pending": [{"call": …, "token": …}]`);
  the engine accepts the outcome only with the token of a wait, once, and then handles it like any
  event, by the arrows of the message the user is on then (the root section handles it anywhere).
  Moves keep the waits; a new call of the same action replaces its wait unless the action is
  declared with `"every": true`. `restart` clears the waits, recovery keeps them (decision 12).
  The designer draws the screen for each outcome: in the pizza example, creating the order may
  be rejected because of the address or fail, and each leads to its own message.
- Arrows without a target: a variant without `to` performs its actions without a move — no exit,
  no entry, no message sent again, timers and places unchanged; a timer firing handled this way is
  consumed. On entering the bot (recovery, `start` with an event) such a variant leads to the
  initial message (decision 13).
- `figram check`: an arrow on an outcome an action does not have, an action with outcomes twice in
  one `do`, invalid outcome names, `every` without `outcomes`, `resume` or `back` without `to` are
  errors; a variant that waits for an outcome on a message without an arrow for it is a warning.
- Proofs ([docs/proofs.md](docs/proofs.md)): an event the snapshot does not accept changes nothing
  (`step_rejected`); a step keeps every other wait whatever the move (`step_keeps_wait`,
  `step_pending_new`), records the waits of its calls with its token (`step_records_wait`) and a
  latest-only call replaces the older wait (`step_latest_replaces`); tokens are fresh
  (`step_fresh`) and each call is answered once (`step_outcome_once`); a variant without a target
  moves nothing (`step_stay`, `stay_keeps`, `enterBot_stay`); background actions do not affect the
  machine (`step_undetach`, `startWith_undetach`, `recover_undetach`); the arrow is looked up by
  exactly the event the host sent (`decEvent_key`, `decEvent_outcome`), `start` takes no outcome
  event (`decStartEvent_ok`), `restart` clears the waits (`handle_restart`, `restart_pending`).
  `compile_correct` covers all three features; 70 theorem statements are pinned.
- `figram trace … --directed`: after the random scenarios, the shortest scenarios a search over
  the reachable places finds for the arrow variants they missed; the random scenarios are guided
  towards uncovered variants. `verify.sh` requires every arrow variant of the examples covered.
- Mutants of the new features: `detach-flip`, `wait-free`, `wait-swap` (latest ↔ every) of an
  action, `stay-reenter` of a variant without a target.

### Changed

- Protocol version 3: `hello` replies `"protocol": 3`. Snapshots carry `pending`, `call` actions
  carry `token` (an action with outcomes) and `detached`, and requests may send outcome events with
  `token`; an engine or a validator of version 2 rejects them, so roll the engine back only
  together with the snapshots.
- Event names starting with `call:` are reserved for outcomes: `start` rejects them, and a
  `step` with such an event needs `token`.
- The pinned statements of `startWith_eq_step`, `startWith_initial`, `startWith_unmatched`,
  `enterBot_eq_realize_some`, `enterBot_eq_realize_none` (the snapshot has waits; an outcome is
  matched only with a wait) and `toChart_decls` (the chart depends on the declarations of actions
  through their properties) changed deliberately.
- The examples: pizza creates the order with outcomes and notifies the kitchen in the background;
  features shows background actions, outcomes with `every`, arrows without a target and a timer
  handled without a move. `examples/golden.jsonl` has 37 scenarios.

## [0.2.0] - 2026-10-25

### Added

- `start` with the user's first event: the optional fields `event` and `guards`, and the reply
  `need` as for `step`. The event is matched as if the user stood on the initial message, which
  is neither entered nor left: a chosen variant gives its actions and the entry into its target
  in one reply with one `seq`; without a variant the reply is that of `start`
  ([docs/format.md](docs/format.md), section 7, decision 9, and [docs/format.md](docs/format.md),
  section 8). A deep link
  (`/start ref123` with `"if": "ref_valid"`) or a button of an old message now works for a new
  user, and the host no longer treats `cmd:start` specially.
- Proofs of it: the compiled machine equals the reference semantics (`startWith_correct`), it is
  a step from the initial message without entering it (`startWith_eq_step`), a variant into the
  initial message enters it once (`startWith_initial`), `need` is sufficient
  (`missingStart_nil`) and the protocol executes it (`handle_start`).
- "Back to the previous section": the variant flag `"back": true` leads back to the message the
  user left when they last crossed a section boundary, wherever they came from; a move between
  messages of one section keeps that place, and "back" from it returns again. Without a place
  (a new user, or its message was removed), the variant leads to its `to`, with `resume` as
  usual. The snapshot gets the optional field `"back": <message>`; `restart` clears it, the first
  entry and recovery keep it ([docs/format.md](docs/format.md), section 7, decision 10). A reader
  of version 0.1.0 rejects a document with `back` as an unknown key.
- Proofs of it: the place after a move (`fire_back`), "back" and back again (`fire_back_swap`),
  the place is always a message the user left (`step_back`), entering the bot keeps it
  (`enterBot_back`), the compiled machine compares the chart's sections (`compile_zoneOf`), and
  `compile_correct` covers it; 46 theorem statements are pinned ([docs/proofs.md](docs/proofs.md)).
- Python client: `Engine.start(key, seq, event, guards)`.
- `figram check` for "back": a "back" arrow counts for reachability as leading to the initial
  message (its real target is a message the user has already been on), and warning 3 ("resume"
  drawn only from inside the section) ignores sections that only the fallbacks of "back" resume.

### Fixed

- Leaving a message, recovery and `restart` took time quadratic in the number of the snapshot's
  timers: a snapshot of 1 MiB with about 96 000 timers kept the engine busy for about a minute
  (the engine never writes such a snapshot; a corrupted or forged one is enough). The machine now
  clears the snapshot's timers at once, linear in the size of the snapshot (80 000 timers:
  0.2 s instead of 42 s), and still computes exactly the specification `realize`
  (`realize_clear`, `fire_eq_realize`, `enterBot_eq_realize_some`, `enterBot_eq_realize_none`,
  `recover_eq_realize`;
  [docs/proofs.md](docs/proofs.md)). `tests/engine/snapshot_scaling.py` checks the growth.

### Changed

- Protocol version 2: `hello` replies `"protocol": 2`. The Python client requires version 2.
- Snapshots now carry `back` after a move across a section boundary, also for documents without
  "back" arrows; the Python and Go client validators accept it. An engine of version 0.1.0
  rejects such a snapshot as an unknown field, so roll the engine back only together with the
  snapshots.
- The pizza example: "Back" on the help message is now `back` (to the message the user opened
  help from, otherwise the menu) instead of "resume the order". Help is in a section of its own
  (`help_section`): directly in the root section next to `menu` and `done`, opening it there
  would cross no boundary, and "Back" would return to an old place in the order (the cart of a
  cancelled order, the payment of a finished one). The features example gained
  "back" arrows (`btn:prev` on the root section, `btn:back` with `resume` on `paid`) and eight
  golden scenarios.
- Recovery processes the event: a step from a snapshot whose message was removed from the
  document (`"recovered": true`) cancels the old timers, keeps the remembered places and then
  handles a named event as `start` with that event does, so it may ask `need`; before, the event
  was dropped and the user always landed on the initial message. A timer event still gives the
  plain start over (theorems `step_recover`, `step_recover_timer`; [docs/format.md](docs/format.md),
  section 7, decision 8). A host that delivered the event again after `"recovered": true`, as the
  0.1.0 host contract allowed, must stop: the second delivery processes the event once more
  (with the idempotency keys `user:seq:index` its actions run twice)
  ([docs/host-contract.md](docs/host-contract.md), section 6).
- The scenarios of `examples/golden.jsonl` (`figram golden`, shipped in the archive): `start`
  may carry `event` and `guards`, the request is then `start` with that event (seven
  scenarios: `start_event_*` and `back_after_start_event`); a replayer that ignores them sends the
  plain `start` and gets other replies.
- The validators of the Python and Go clients accept `"recovered": true` only in a reply to
  `step`.
- Documentation: a host thread that calls the library needs up to about 68 KiB of stack at the
  parser's nesting limit, give it at least 128 KiB ([docs/embedding.md](docs/embedding.md));
  `restart` with a placeholder snapshot drops the update's event, `step` with the placeholder
  and the event processes it ([docs/host-contract.md](docs/host-contract.md), section 4).
- The reference host `tests/engine/host_sim.py` starts a user without a snapshot with the
  update's event and the high-water mark, drops a timer event of a lost snapshot, and tests a
  lost snapshot with two controls ([docs/host-contract.md](docs/host-contract.md), sections 1
  and 7).

### Known issues

- The JSON reader finds duplicate keys of an object with a hash set: O(1) per key on average,
  but keys chosen so that their hashes collide make one request take time quadratic in the
  number of keys of one object (about 12 s within the default limit of 1 MiB). It is in 0.1.0
  as well, and the fix (a check that does not depend on the hash) comes with the next release.
  Until then a host that does not trust its stored snapshots can lower `--max-request` (the
  time falls with the square of the limit) and run more than one engine process, so that one
  slow request does not hold up the requests of other users
  ([docs/host-contract.md](docs/host-contract.md), section 5).

## [0.1.0] - 2026-10-21

First release. It follows an independent audit and fix cycle; the audit reports, logs and
harnesses are not part of the tree and are preserved at commit
[`16c885a`](https://github.com/Ruddy35/figram-fsm-library/tree/16c885a). The tests written during
the audit were kept under `tests/`.

### Added

- Document format version 1 for chat-bot state machines: nested sections, arrows with ordered
  guarded variants, actions, "resume where the user left off", timers, annotations and declared
  extensions ([docs/format.md](docs/format.md)); JSON schemas for the document and the snapshot.
- `figram` CLI: `example`, `check` (errors and warnings), `fmt`, `stats`, `trace`, `random`,
  `golden`.
- `figram-engine`: the stateless engine process with protocol version 1 (`hello`, `start`,
  `restart`, `step`), strict requests, error `code`s, a request size limit and strict UTF-8.
- `libfigram.a`: the engine as a static library with a C ABI (`ffi/figram.h`); the runtime starts
  without the libuv event loop and creates no threads, file descriptors or signal handlers.
- Reference clients: a Python client with a process pool (`clients/python`) and a Go cgo wrapper
  (`clients/go`).
- Lean proofs of the compiler, the document format and the engine protocol; 23 theorem
  statements are pinned and checked, together with the axioms of all theorems, by `./verify.sh`
  ([docs/proofs.md](docs/proofs.md)).
- Test suites: the full gate `./verify.sh`, formats, compiler oracle, C ABI sanitizers and
  mutants, Go tests ([docs/testing.md](docs/testing.md)).
- Release archives for Linux x86_64 with SHA-256 checksums ([docs/releasing.md](docs/releasing.md)).

### Changed

- Compared with the pre-release commits, all user-facing texts are in English: engine error
  messages, `figram` diagnostics and CLI output, C ABI error texts, the Go wrapper and the Python
  client (its `EngineError` messages). Error `code` values, JSON field names, CLI command names
  and exit statuses are unchanged.
- An unknown key without the `x-` prefix in the document, a section, a message or a variant is an
  error: `figram check`, `figram_check_json` and document loading (`figram-engine`,
  `figram_load_doc_ex`) reject it, and the document schema allows only `x-` keys besides the
  format keys. Before, such keys were accepted and kept silently, so a misspelt
  optional field (`"iff"`, `"tiemrs"`) changed the bot without any message. `figram fmt` still
  keeps them ([docs/format.md](docs/format.md), section 5).

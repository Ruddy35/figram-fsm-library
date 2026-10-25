# Changelog

All notable changes to this project are documented in this file. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Clients rely on the document and
snapshot formats, the engine protocol (operations, JSON field names, error `code` values), the CLI
command names and the exit statuses; the texts of messages are for humans and may change.

## [Unreleased]

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
- The reference host `tests/engine/host_sim.py` starts a user without a snapshot with the
  update's event and the high-water mark, drops a timer event of a lost snapshot, and tests a
  lost snapshot with two controls ([docs/host-contract.md](docs/host-contract.md), sections 1
  and 7).

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

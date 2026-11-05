# Changelog

All notable changes to this module are documented in this file. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/). The module version follows the Figram
release whose libraries it bundles.

## [0.3.0] - 2026-11-04

Bundles the libraries of
[Figram v0.3.0](https://github.com/Ruddy35/figram-fsm-library/releases/tag/v0.3.0), which speak
protocol version 3. Not compatible with Figram v0.2.0 or v0.1.0: use ffl-go v0.2.0 or v0.1.0
with them.

### Added

- `Bot.Outcome(snapshot, action, outcome, token, guards)`: deliver the outcome of a call of an
  action with outcomes (the event `call:<action>:<outcome>` with the call's token). The engine
  accepts it once, while the snapshot waits for that call, and handles it by the arrows of the
  message the user is on; a stale or repeated outcome changes nothing. `Need` and `Decide` work
  as for `Step`. A malformed outcome (empty, or with `:`) is refused before it is sent.
- `Snapshot.Pending` (JSON `pending`, a list of `Wait{Call, Token}`, newest first): the calls
  whose outcome is awaited. Store it with the rest of the snapshot; `Restart` clears it.
- `Action.Outcomes` and `Action.Token` of a `Call` (the action has outcomes: deliver one with
  that token) and `Action.Detached` (a background action: run it later and in any order).
- `CodeSnapshotTooLarge` (`snapshot_too_large`): under the request size limit the engine refuses
  a step whose new snapshot would take more than 15/16 of the limit; the stored snapshot still
  works for its outcomes and `Restart`.
- Tests: the golden scenarios of v0.3.0 (outcomes with their tokens, background actions,
  arrows without a target), `Outcome` with a stale, an accepted and a repeated token and with
  `Decide`, the snapshot size limit, the protocol 3 rows of the reply validator, an example, and
  seven more wrapper mutants (23).

### Changed

- The libraries, the header and the test data of Figram v0.3.0 (`BundledVersion` 0.3.0). The
  runtime of v0.3.0 reserves a 64 MiB arena when the address space allows it (about 64 MiB more
  address space, no more memory in use).
- `ProtocolVersion` is 3; `Load` rejects a library of another protocol version. The reply
  validator accepts `pending` in snapshots and `token` and `detached` in `call` actions.

## [0.2.0] - 2026-10-26

Bundles the libraries of
[Figram v0.2.0](https://github.com/Ruddy35/figram-fsm-library/releases/tag/v0.2.0), which speak
protocol version 2. Not compatible with Figram v0.1.0: use ffl-go v0.1.0 with it.

### Added

- `Bot.StartWith(seq, event, guards)`: start a new user with the update's event (a command, a
  button of an old message). The event is matched as if the user stood on the initial message;
  the result may have `Need`, as for `Step`, and `Decide` works with it. A timer event is a
  `bad_request`.
- `Snapshot.Back` (JSON `back`): the message "back" arrows lead to, which the engine writes after
  a move across a section boundary. Store it with the rest of the snapshot.
- `ProtocolVersion` (2); `Load` rejects a library that speaks another protocol version (possible
  with the build tag `figram_system`).
- Tests: the golden scenarios of v0.2.0 (`start` with an event, "back"), `StartWith` with `Need`
  and `Decide`, a recovered step that processes its event, and six more wrapper mutants (16).

### Changed

- The libraries, the header and the test data of Figram v0.2.0 (`BundledVersion` 0.2.0).
- A reply to `start` may be `need`; `Hello.Protocol` is 2.
- `Result.Recovered`: the event of a recovered step is processed now (the old timers are
  cancelled and the remembered places kept); do not deliver it again.

## [0.1.0] - 2026-10-21

First release, bundling the libraries of
[Figram v0.1.0](https://github.com/Ruddy35/figram-fsm-library/releases/tag/v0.1.0).

### Added

- Package `figram`: `Init`, `SetMaxRequest`, `Check`, `Fmt`, `Load`; `Bot` with `Start`,
  `Restart`, `Step`, `Timer`, `Hello`, the raw `Request` and `Close`; `Decide` for conditions.
- Typed protocol values `Snapshot`, `Action`, `Result`, `Hello` with their JSON forms, and the
  errors `DocumentError`, `ProtocolError` (with the protocol's codes) and `ErrClosed`. Every
  reply of the engine is checked strictly.
- The static libraries of Figram v0.1.0 for linux/amd64 in `lib/linux_amd64` (vendoring
  supported), and the build tag `figram_system` for a library found with pkg-config.
- Tests: the golden scenarios of the release, concurrency and `Close` under load, protocol
  errors, process tests (lazy start, signal dispositions, RLIMIT_AS), runnable examples, a
  consumer-module build test and wrapper mutants; `scripts/update-lib.sh` to update the bundled
  release and check it.

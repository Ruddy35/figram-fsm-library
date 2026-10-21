# Changelog

All notable changes to this project are documented in this file. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Clients rely on the document and
snapshot formats, the engine protocol (operations, JSON field names, error `code` values), the CLI
command names and the exit statuses; the texts of messages are for humans and may change.

## [0.1.0] - Unreleased

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

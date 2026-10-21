# Figram

A finite-state-machine engine for chat bots, written in Lean 4, with machine-checked proofs of
its compiler, document format and engine protocol.

[![CI](https://github.com/Ruddy35/figram-fsm-library/actions/workflows/ci.yml/badge.svg)](https://github.com/Ruddy35/figram-fsm-library/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/Ruddy35/figram-fsm-library?include_prereleases&sort=semver)](https://github.com/Ruddy35/figram-fsm-library/releases)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

## Overview

A bot is one JSON document (`examples/*.bot.json`, schema
[`schemas/figram.schema.json`](schemas/figram.schema.json)), meant to be written by a visual
editor (not part of this repository) or by hand; the engine executes the document. The document
describes messages, events, arrows with ordered variants guarded by named conditions, actions,
nested sections, "resume where the user left off" arrows, and timers
([docs/format.md](docs/format.md)).

The engine is **stateless**. It loads and compiles a document once and then answers requests:
given a user's snapshot and an event, it returns the new snapshot and the actions to perform
(send a message, call a developer action, start or cancel a timer). The engine never stores
users. The **host** — the developer's code around the engine — keeps one small JSON snapshot
per user, evaluates conditions when the engine asks for them, executes the actions, and
schedules timers ([docs/host-contract.md](docs/host-contract.md)).

**What is proved.** The reference semantics of the designer's chart, the compiler to a flat
machine and the document format are specified in Lean 4; 23 pinned theorems state, among other
things, that the compiled machine takes exactly the steps of the chart for every snapshot, event
and condition values, that a checked document is encoded and decoded back unchanged, and that a
`step` request runs exactly the proved step and returns a snapshot the engine can read back. All
theorems use only the standard axioms `propext`, `Quot.sound` and `Classical.choice`, and the
Lean kernel re-checks every declaration of the `Figram` library (`leanchecker`). The JSON text
layer, the string-level request handler, the CLI, the C shim and the clients are not proved;
they are tested ([docs/proofs.md](docs/proofs.md), [docs/testing.md](docs/testing.md)).

The proved Lean code is what runs: it is compiled into

* `figram` — a command-line tool to check, format and explore documents;
* `figram-engine` — the engine as a process speaking line-delimited JSON over stdin/stdout;
* `libfigram.a` — the engine as a static library with a C ABI ([`ffi/figram.h`](ffi/figram.h)),
  used from C, C++ and Go.

## Features

* One JSON document per bot, with a strict JSON parser, a canonical form (`figram fmt`) and
  extension rules: annotations under `x-` keys (and any other unknown keys) are kept and never
  change behaviour; semantic extensions must be declared in `requires`.
* `figram check` reports the errors that the structure cannot rule out and warns about probable
  mistakes, such as variants that can never fire, unreachable messages and self-defeating timer
  loops.
* A versioned, strict engine protocol: replies in request order, machine-readable error `code`s,
  a request size limit, strict UTF-8.
* Conditions are evaluated by the host only when the engine needs them (`need`), and only the
  conditions that can change the choice are requested.
* Timers carry tokens, so a stale firing is ignored; the `seq` transition counter is the basis of
  the host's idempotency keys.
* Embedding with minimal side effects on the host process: initialization creates no threads,
  file descriptors or signal handlers (the `SIGPIPE` disposition that the Lean runtime changes is
  restored), and works under `RLIMIT_AS` ([docs/embedding.md](docs/embedding.md)).
* Reference clients: a Python client with a pool of engine processes, and a Go cgo wrapper.

## Quick start

### Download a release

Releases are published on the [releases page](https://github.com/Ruddy35/figram-fsm-library/releases)
for Linux x86_64:

* `figram-<version>-linux-x86_64.tar.gz` — the `figram` and `figram-engine` executables, the
  example documents, the JSON schemas and the documentation (this README, `docs/`, `CHANGELOG.md`,
  `LICENSE`, `THIRD_PARTY_NOTICES.md`);
* `libfigram-<version>-linux-x86_64.tar.gz` — `figram.h`, `libfigram.a` and the Lean runtime
  archives needed to link it, with the same documentation;
* `SHA256SUMS` — the checksums of both archives; `SHA256SUMS.binaries` — the checksums of the two
  executables.

```sh
VERSION=0.1.0
BASE=https://github.com/Ruddy35/figram-fsm-library/releases/download/v$VERSION
curl -LO "$BASE/figram-$VERSION-linux-x86_64.tar.gz" -LO "$BASE/SHA256SUMS"
sha256sum --ignore-missing -c SHA256SUMS
tar xzf "figram-$VERSION-linux-x86_64.tar.gz"
cd "figram-$VERSION-linux-x86_64"
bin/figram check examples/pizza.bot.json
```

The executables need Linux x86_64 with glibc and depend on no other shared library.
[docs/releasing.md](docs/releasing.md)
describes how releases are built and how to check that they were built from the sources.

### Build from source

```sh
lake build            # needs Lean 4.35.0, see "Building from source"
.lake/build/bin/figram check examples/pizza.bot.json
```

## Usage

The examples below assume that `figram` and `figram-engine` are on `PATH`
(`export PATH=$PWD/.lake/build/bin:$PATH` after building).

### CLI: `figram`

| Command | Description |
|---|---|
| `figram example` | print the example pizza-shop document |
| `figram check <bot.json>` | check a document: prints `ok`, or the errors one per line (exit 1); warnings go to stderr with the prefix `warning:` and exit 0; a document that cannot be parsed or decoded is reported on stderr (exit 1) |
| `figram fmt <bot.json>` | print the canonical form of a document (the document is decoded, not checked) |
| `figram stats <bot.json>` | print the size of the compiled machine |
| `figram trace <bot.json> <count> <length> <seed>` | print `count` random reference scenarios (JSON Lines) computed from the proved chart semantics; the coverage of arrow variants goes to stderr |
| `figram random <seed> <messages>` | print a random valid document |
| `figram golden <dir>` | write the fixed reference scenarios and their documents into `dir` |

`figram` exits with status 0 on success and 1 on any error, including invalid arguments; the
reason goes to stderr. Use `/dev/stdin` as the file name to read a document from standard input.
The hidden command `figram json` parses lines of stdin with the JSON text layer; it exists for
the differential tests.

```sh doctest
$ figram check examples/pizza.bot.json
ok
$ figram stats examples/pizza.bot.json
{"states":11,"rows":40,"branches":42,"resumes":1,"actions":31}
$ figram example | cmp - examples/pizza.bot.json && echo identical
identical
$ echo '{"bot":{"children":{"start":{}},"initial":"start"},"figram":1}' | figram fmt /dev/stdin
{
  "figram": 1,
  "bot": {
    "initial": "start",
    "children": {
      "start": {}
    }
  }
}
$ figram trace examples/pizza.bot.json 1 3 7 2>/dev/null
{"start":{"snapshot":{"at":"menu","seq":1},"actions":[{"send":"menu"}]},"steps":[{"event":"btn:order","guards":{},"snapshot":{"at":"catalog","resume":{"order":"catalog"},"seq":2},"actions":[{"send":"catalog"}]},{"event":"btn:cart","guards":{},"snapshot":{"at":"cart","resume":{"order":"cart"},"timers":{"cart_reminder":3},"seq":3},"actions":[{"start_timer":"cart_reminder","ms":7200000,"token":3},{"send":"cart"}]},{"event":"cmd:help","guards":{},"snapshot":{"at":"help","resume":{"order":"cart"},"seq":4},"actions":[{"cancel_timer":"cart_reminder"},{"send":"help"}]}]}
$ figram random 7 5 2>/dev/null | figram check /dev/stdin 2>/dev/null
ok
$ out=$(mktemp -d) && figram golden "$out" > /dev/null && ls "$out" && rm -r "$out"
features.bot.json
golden.jsonl
pizza.bot.json
```

A document with an error (an arrow to a message that does not exist):

```sh doctest
$ echo '{"figram":1,"bot":{"initial":"a","children":{"a":{"on":{"btn:x":[{"to":"ghost"}]}}}}}' | figram check /dev/stdin 2>/dev/null
arrow `a` → `ghost`: target does not exist
[exit 1]
```

### Engine: `figram-engine`

```text
figram-engine [--max-request BYTES] <bot.json>
```

The engine loads and compiles the document, then reads one JSON request per line from stdin and
writes one JSON reply per line to stdout, strictly in request order. It exits with status 0 at
the end of input, 1 if the document cannot be loaded (the reasons go to stderr), and 2 on invalid
arguments. `--max-request` sets the request size limit (default 1 MiB, 0 — no limit). The
process is single-threaded and serves one request at a time; run several processes for
parallelism.

```sh doctest
$ printf '%s\n' '{"id":1,"op":"start"}' '{"id":2,"op":"step","snapshot":{"at":"menu","seq":1},"event":"btn:order"}' \
>   | figram-engine examples/pizza.bot.json
{"id":1,"snapshot":{"at":"menu","seq":1},"actions":[{"send":"menu"}]}
{"id":2,"snapshot":{"at":"catalog","resume":{"order":"catalog"},"seq":2},"actions":[{"send":"catalog"}]}
```

The operations are `hello`, `start`, `restart` and `step`; a `step` may answer
`{"need": [...]}` when it needs condition values, and errors carry a machine-readable `code`
(`parse_error`, `too_large`, `bad_request`, `bad_snapshot`, `seq_overflow`). The complete
protocol, the snapshot format and the error rules are in [docs/format.md](docs/format.md),
section 8; the rules a host must follow (per-user ordering, transactions, idempotency, timers,
document updates) are in [docs/host-contract.md](docs/host-contract.md).

### Embedding from C and Go

`bash ffi/build.sh` builds `ffi/out/libfigram.a` and writes the linker flags to
`ffi/out/LDFLAGS`:

```sh
cc host.c -I ffi $(cat ffi/out/LDFLAGS)
```

The C ABI (initialization, ownership, strings, threads, side effects, memory limits), a complete
C example and the Go wrapper in [`clients/go`](clients/go) are described in
[docs/embedding.md](docs/embedding.md).

### Python client

[`clients/python/figram_client.py`](clients/python/figram_client.py) drives a pool of
`figram-engine` processes with pipelined requests, timeouts, replacement of failed processes and
restart backoff; failures are reported as `EngineError` with `code` and `retryable`
([docs/host-contract.md](docs/host-contract.md), section 5).

```python doctest
from figram_client import Engine, EngineError

engine = Engine("figram-engine", "examples/pizza.bot.json", processes=2)
try:
    user = "user-42"
    reply = engine.start(user)
    print(reply["actions"])
    reply = engine.step(user, reply["snapshot"], "btn:order")
    print(reply["snapshot"], reply["actions"])

    # The engine asks for conditions only when it needs them; the client calls `guards`.
    cart = {"at": "cart", "seq": 3}
    reply = engine.step(user, cart, "btn:checkout",
                        guards=lambda names: {name: True for name in names})
    print(reply["snapshot"]["at"])

    try:
        engine.step(user, {"at": "cart", "vv": 1}, "btn:checkout")
    except EngineError as e:
        print(e.code, e.retryable)
finally:
    engine.close()
```

```text output
[{'send': 'menu'}]
{'at': 'catalog', 'resume': {'order': 'catalog'}, 'seq': 2} [{'send': 'catalog'}]
address
bad_snapshot False
```

## Documentation

| Document | Contents |
|---|---|
| [docs/format.md](docs/format.md) | the document format, its derivation and extension rules, the execution semantics, the snapshot and the engine protocol |
| [docs/host-contract.md](docs/host-contract.md) | what a host must do: ordering, transactions, idempotency, timers, resets, engine failures, document updates |
| [docs/embedding.md](docs/embedding.md) | the C ABI and the Go wrapper |
| [docs/proofs.md](docs/proofs.md) | the pinned theorems, how proofs are checked, what is not proved |
| [docs/testing.md](docs/testing.md) | `./verify.sh` step by step, the other suites, reachability checks, performance |
| [docs/releasing.md](docs/releasing.md) | how releases are built, checked and published |
| [CHANGELOG.md](CHANGELOG.md) | release notes |
| [CONTRIBUTING.md](CONTRIBUTING.md) | development setup and conventions |
| [SECURITY.md](SECURITY.md) | reporting vulnerabilities |
| [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) | components linked into the binaries and their licenses |

## Building from source

Requirements (Linux x86_64 only):

* **Lean 4.35.0.** With [elan](https://github.com/leanprover/elan), `lake build` uses the
  version pinned in `lean-toolchain` (`leanprover/lean4:v4.35.0`). Without elan,
  `ci/install-lean.sh [DIR]` downloads the official release archive, checks its SHA-256 against
  `ci/lean-toolchain.sha256`, unpacks it into `DIR` (default `~/.lean-toolchain`) and prints the
  `bin` directory to put on `PATH`; it needs `curl`, `zstd` and `tar`. A Lean 4.35.0 built from
  source with gcc also works; then `ffi/build.sh` needs the static `libuv` and `libgmp`
  (Debian/Ubuntu: `apt install libuv1-dev libgmp-dev`).
* **For the tests:** Python 3 with the `jsonschema` package, Go 1.23 or newer (`clients/go`),
  gcc and g++ with AddressSanitizer and ThreadSanitizer, binutils (`objdump`, `readelf`, `nm`,
  `c++filt`, `strip`) and `prlimit`.

```sh
lake build                 # figram, figram-engine and the Lean library, without warnings
bash ffi/build.sh          # libfigram.a, ffi/out/LDFLAGS, clients/go/zz_figram_ldflags.go
```

## Testing

```sh
./verify.sh
```

`./verify.sh` is the full gate: build without warnings, the pinned theorem statements and axioms,
the strict proof gate, golden and random scenarios against the engine, mutation testing, the host
contract, the protocol edge cases, differential fuzzing of the JSON text layer, the C ABI, the
reachability of the libuv event loop, the Python client and the Go wrapper. It exits with 0 when
every step passed (about 5 minutes on 4 cores). The formats suite, the compiler oracle suite, the
C ABI sanitizer and mutant suites and the Go tests run separately;
[docs/testing.md](docs/testing.md) describes all of them, and CI
([`.github/workflows/ci.yml`](.github/workflows/ci.yml)) runs all of them with the official Lean
release.

## Project layout

```text
Figram/                 Lean sources: semantics (Chart), core machine (Core), compiler (Compile),
                        format (Format, FormatIO), checks (Lint, Warnings), engine (Engine, Api),
                        examples, scenarios and mutants (Examples, Run, Trace, RandomDoc, Mutate),
                        proofs (*Proofs, Correct)
Main.lean               the figram CLI
EngineMain.lean         the figram-engine process
lakefile.lean           the Lake build (version, targets)
ffi/                    the C ABI: figram.h, the shim (figram_ffi.c, figram_guard.cpp,
                        figram_init.cpp, figram_rt.c), figram_exe.c (start-up of the
                        executables), build.sh
clients/python/         reference Python client (figram_client.py, protocol.py)
clients/go/             reference Go wrapper and the gofig test driver
schemas/                JSON schemas of the document and of the snapshot
examples/               example documents and the golden scenarios
docs/                   documentation
tests/engine/           protocol, conformance, host contract and client suites (Python)
tests/capi/             C ABI tests
tests/formats/          formats suite
tests/compiler/         compiler oracle suite
tests/proofs/           proof gates, pinned theorem statements, supplementary lemmas
tests/lib/              shared test helpers
ci/                     install-lean.sh, lean-toolchain.sha256, reach.py, package.sh,
                        test-bundle.sh, check_docs.py
.github/workflows/      ci.yml (every push), release.yml (draft releases)
verify.sh               the full gate
verify-prebuilt.sh      checks prebuilt binaries without Lean
verify-faults.sh        checks that the gates detect injected faults
```

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Security issues: [SECURITY.md](SECURITY.md).

## License

MIT, see [LICENSE](LICENSE). The binaries also contain third-party components under their own
licenses, see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

## Audit archive

Before the first release the project went through an independent audit and a fix cycle. The
audit reports, logs and harnesses are not part of the tree; they are preserved at commit
[`16c885a`](https://github.com/Ruddy35/figram-fsm-library/tree/16c885a).

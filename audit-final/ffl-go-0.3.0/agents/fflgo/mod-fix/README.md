# ffl-go

[![ci](https://github.com/Ruddy35/ffl-go/actions/workflows/ci.yml/badge.svg)](https://github.com/Ruddy35/ffl-go/actions/workflows/ci.yml)
[![Go Reference](https://pkg.go.dev/badge/github.com/Ruddy35/ffl-go.svg)](https://pkg.go.dev/github.com/Ruddy35/ffl-go)

Go bindings for [Figram](https://github.com/Ruddy35/figram-fsm-library), a finite-state-machine
engine for chat bots. A bot is a JSON document: nested sections, buttons and other events, guarded
arrows, actions (background actions, and actions that report how they ended), "resume where the
user left off", "back to the previous section" and timers. The engine that executes it is
written and proved in Lean 4 and is linked into your program as a static C library: no separate
process, no network, no state of its own.

```go
import figram "github.com/Ruddy35/ffl-go"
```

## Features

* `Load` checks and compiles a document once; a `*Bot` serves all users from many goroutines.
* Stateless and deterministic: the host keeps a small JSON `Snapshot` per user, and the same
  snapshot, event and guards always give the same result.
* Typed protocol (version 3): `Start`, `StartWith` (a new user's first event), `Step`, `Timer`,
  `Outcome` (the outcome of an action), `Restart`, `Hello`; every reply of the engine is checked strictly, and errors carry the
  protocol's `Code` (`bad_snapshot`, `bad_request`, ...).
* `Check` and `Fmt` do what `figram check` and `figram fmt` do (a misspelt key such as `"iff"` is
  an error).
* The libraries of the Figram release are bundled: `go get` is enough on linux/amd64.

## Requirements

* Linux x86_64 with glibc, for the bundled libraries (other platforms: see
  [Other platforms](#other-platforms)).
* Go 1.23 or later with cgo enabled, and a C compiler (gcc or clang).

## Installation

```sh
go get github.com/Ruddy35/ffl-go@v0.3.0
```

## Quick start

```go
package main

import (
	"encoding/json"
	"fmt"
	"log"
	"os"

	figram "github.com/Ruddy35/ffl-go"
)

func main() {
	doc, err := os.ReadFile("pizza.bot.json") // examples/ of the Figram release
	if err != nil {
		log.Fatal(err)
	}
	bot, err := figram.Load(string(doc))
	if err != nil {
		log.Fatal(err) // *figram.DocumentError lists the problems of the document
	}
	defer bot.Close()

	res, err := bot.Start(0) // a new user
	if err != nil {
		log.Fatal(err)
	}
	res, err = bot.Step(res.Snapshot, "btn:order", nil) // the user pressed "Order"
	if err != nil {
		log.Fatal(err)
	}
	stored, _ := json.Marshal(res.Snapshot) // keep it for the user's next event
	fmt.Println(string(stored))             // {"at":"catalog","back":"menu","resume":{"order":"catalog"},"seq":2}
	for _, a := range res.Actions {
		fmt.Println(a.Kind, a.Name) // send catalog
	}
}
```

The runnable examples of the package (`example_test.go`) cover conditions and timers too.

## Usage

**Snapshots.** Store `res.Snapshot` as JSON (for example in a database row of the user) and pass
it back with the user's next event. The engine rejects a snapshot it does not understand with
`bad_snapshot`, so a snapshot is never misread. Store it as the engine returned it: fields such
as `Back` (where "back" leads) are part of the user's state.

**New users.** A user without a snapshot who comes with an update (a command such as
`/start ref123`, or a button of an old message) is started with
`bot.StartWith(seq, event, guards)`: the event is processed as if the user stood on the initial
message, so a deep link or an old button leads straight to its target. `bot.Start(seq)` starts a
user without an event. `seq` is the user's high-water mark (0 for a user who never had a
snapshot).

**Recovery.** If the snapshot's message is no longer in the document (it was edited), `Step`
cancels the old timers, keeps the remembered places and processes the event as `StartWith`
does; the result has `Recovered` set. Do not deliver the event again after that.

**Actions.** Execute `res.Actions` in order after storing the snapshot:

| `Kind` | Meaning |
|---|---|
| `figram.Send` | send the message `Name` to the user |
| `figram.Call` | run your action `Name` (an order, a payment, ...); `Outcomes`: deliver its outcome with `Token`; `Detached`: a background action |
| `figram.StartTimer` | start the timer `Name` for `Ms` milliseconds; keep `Token` |
| `figram.CancelTimer` | cancel the timer `Name` |

**Timers.** When a timer fires, call `bot.Timer(snapshot, name, token, nil)` with the token of
its `start_timer` action. A stale token, or a timer of a message the user already left, changes
nothing.

**Outcomes.** An action declared with `"outcomes"` in the document reports how it ended. Its
`Call` has `Outcomes` set and a `Token`, and the snapshot waits for it (`Snapshot.Pending`).
After performing the action, deliver exactly one outcome:
`bot.Outcome(snapshot, a.Name, "created", a.Token, nil)` with a declared outcome, or `"failed"`
when you give up without an answer (reconcile with the external system first: the host
contract, section 2). The outcome is handled by the arrows of the message the user is on then;
a stale or repeated outcome changes nothing. A `Call` with `Detached` set is a background action:
run it later and in any order, the user's next event does not wait for it.

**Conditions.** An arrow can depend on conditions (`"if": "cart_not_empty"`) that only the host
can evaluate. Pass known values in `guards`; if the step (or `StartWith`) needs others, the
result has only `Need` set. `figram.Decide` evaluates the needed conditions with your function and repeats the
step:

```go
res, err := figram.Decide(nil, cartNotEmpty, func(g map[string]bool) (*figram.Result, error) {
	return bot.Step(snap, "btn:checkout", g)
})
```

**Errors.** `*figram.DocumentError` (from `Load`, `Check`, `Fmt`) lists the problems of a
document. `*figram.ProtocolError` is an error reply of the engine: decide by its `Code`
(`figram.CodeBadSnapshot`, `figram.CodeBadRequest`, `figram.CodeSeqOverflow`,
`figram.CodeSnapshotTooLarge`, `figram.CodeTooLarge`, `figram.CodeParseError`), not by the text.
`CodeSnapshotTooLarge` means that the user has as many unanswered waits as the request limit
allows (`SetMaxRequest`): the step is refused, the stored snapshot still works for its outcomes
and `Restart`. After `Close`, calls return
`figram.ErrClosed`.

**Delivery guarantees.** Storing snapshots, executing actions exactly once and handling timers
across retries and crashes follow the
[host contract](https://github.com/Ruddy35/figram-fsm-library/blob/v0.3.0/docs/host-contract.md)
(idempotency keys `user:seq:index`, the order of steps per user).

**Raw protocol.** `bot.Request(line)` sends one request line of the
[engine protocol](https://github.com/Ruddy35/figram-fsm-library/blob/v0.3.0/docs/format.md#8-snapshot-and-engine-protocol-version-3)
and returns the reply line unchecked.

## Building

The bundled libraries make a small program about 17 MB. Most of it is unused Lean runtime code;
the linker drops it with `--gc-sections` (measured: 16.8 MB → 7.2 MB for
`scripts/consumer-test.sh`):

```sh
go build -ldflags=-extldflags=-Wl,--gc-sections
```

cgo does not accept this flag inside the package, so it is passed at build time. Only static
linking into an executable is supported: `-buildmode=c-shared` and plugins cannot be built.

`go mod vendor` works: the libraries live in the package `lib/linux_amd64`, which the module
imports so that vendoring copies it.

## Other platforms

The bundled libraries are for linux/amd64. Elsewhere build the library from the Figram sources
(`ffi/build.sh`, see the
[embedding guide](https://github.com/Ruddy35/figram-fsm-library/blob/v0.3.0/docs/embedding.md))
and build with the tag `figram_system`, which takes the library from pkg-config:

```sh
PKG_CONFIG_PATH=/path/to/libfigram/lib/pkgconfig go build -tags figram_system
```

The same tag links the release archive `libfigram-0.3.0-linux-x86_64.tar.gz` unpacked anywhere
(its `lib/pkgconfig/figram.pc` is relocatable). The Go build cache does not notice another
library behind `PKG_CONFIG_PATH`: after switching to another library, rebuild with `go build -a`
(or `go clean -cache`).

## Versioning

The module version follows the Figram release whose libraries it bundles (`figram.BundledVersion`):
ffl-go v0.3.0 bundles Figram v0.3.0 and speaks protocol version 3 (`figram.ProtocolVersion`);
`Load` rejects a library of another protocol version, which the build tag `figram_system` could
link. ffl-go v0.2.0 speaks protocol 2 and works only with Figram v0.2.0, ffl-go v0.1.0 protocol 1
with Figram v0.1.0. `scripts/update-lib.sh VERSION` replaces the libraries, the header and the test
data with those of another release, verified with the release's `SHA256SUMS`;
`scripts/update-lib.sh --check` (run by CI) checks that the tree equals the release byte for byte.

## Development

```sh
go test ./...                  # unit tests, golden scenarios, examples, process tests
go test -race ./...
scripts/consumer-test.sh       # a separate module: plain, vendored and --gc-sections builds
python3 scripts/mutants.py     # deliberate defects of the wrapper must fail the tests
scripts/update-lib.sh --check  # the bundled files equal the release
```

## Releasing

1. `scripts/update-lib.sh X.Y.Z` with the Figram release, add the section `## [X.Y.Z] - DATE` to
   `CHANGELOG.md`, merge to `master` and wait for `ci`.
2. **Actions → release → Run workflow** on `master` with the version `X.Y.Z`. The workflow
   checks that `BundledVersion`, `lib/linux_amd64/VERSION` and the CHANGELOG section match the
   version and that an existing tag `vX.Y.Z` points at the commit, runs the checks of
   [Development](#development), and creates (or updates) the draft release `vX.Y.Z` with the
   CHANGELOG section, the install line and a provenance block (commit, the release's
   `SHA256SUMS`, the workflow run). Only its last job, which runs no code of the repository, can
   write to the repository.
3. Review the draft and publish it: publishing creates the tag `vX.Y.Z`, which is the module
   version. A published version is final (the Go module proxy keeps it); fix problems with a new
   version. The workflow never publishes a release and refuses to change a published one.

## License

MIT ([LICENSE](LICENSE)). The bundled static libraries in `lib/linux_amd64` are the unchanged
archives of the Figram release and contain third-party components (the Lean 4 runtime, mimalloc,
libuv, GNU MP, LLVM libc++); a program built with this module contains the parts it uses. See
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

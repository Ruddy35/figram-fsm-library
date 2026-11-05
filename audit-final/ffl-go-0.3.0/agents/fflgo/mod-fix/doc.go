// Package figram embeds the Figram engine in a Go program: finite-state machines for chat
// bots, compiled from a JSON document and executed by code written and proved in Lean 4. The
// engine is linked in as a static C library; there is no separate process.
//
// A bot is one JSON document ([format]). [Load] checks and compiles it into a [Bot]. The engine
// keeps no users: for every event the host passes the user's [Snapshot] and gets back the new
// snapshot and the [Action]s to execute.
//
//	bot, err := figram.Load(doc)
//	res, err := bot.Start(0)                            // a new user
//	res, err = bot.Step(res.Snapshot, "btn:order", nil) // an event of that user
//
// A new user who comes with an update (a command, a button of an old message) is started with
// [Bot.StartWith] and the update's event, which the start processes.
//
// Store res.Snapshot (its JSON encoding), then execute res.Actions in order: send messages, run
// the host's actions, start and cancel timers. When a timer fires, call [Bot.Timer] with the name
// and token of its start_timer action. A call of an action with outcomes ([Action.Outcomes])
// carries a token, and the snapshot waits for it ([Snapshot.Pending]): after performing the
// action, pass exactly one outcome to [Bot.Outcome] with that token. A background action
// ([Action.Detached]) may run later and in any order. When the choice of a step depends on conditions the host
// did not pass, the result only lists them in [Result.Need]; [Decide] evaluates them and repeats
// the step. The rules a host follows to keep snapshots, actions and timers consistent under
// retries and crashes (idempotency keys, ordering) are in the [host contract].
//
// A Bot is safe for concurrent use; one Bot serves all users of a bot. The same snapshot, event
// and guards always give the same result.
//
// # Building
//
// The package uses cgo and needs a C compiler (gcc or clang). On linux/amd64 it links the static
// libraries of the Figram release named by [BundledVersion], kept in lib/linux_amd64. To leave
// out the unused runtime code (about 17 MB → 7 MB for a small program), build with
//
//	go build -ldflags=-extldflags=-Wl,--gc-sections
//
// With the build tag figram_system the library is found with pkg-config instead (figram.pc of
// the release archive libfigram-<version>-linux-x86_64.tar.gz, or of a build from source on
// another platform). Only static linking into an executable is supported: -buildmode=c-shared
// and plugins cannot be built.
//
// [format]: https://github.com/Ruddy35/figram-fsm-library/blob/v0.3.0/docs/format.md
// [host contract]: https://github.com/Ruddy35/figram-fsm-library/blob/v0.3.0/docs/host-contract.md
package figram

# Testing

All checks run locally with Lean 4.35.0, Go and Python 3 with `jsonschema` (see the README,
"Building from source"). The main entry point is `./verify.sh`; the larger suites are run
separately and in CI.

| Suite | Command | What it covers |
|---|---|---|
| Full gate | `./verify.sh` | build, proofs, engine, host contract, client, C ABI, Go (below) |
| Prebuilt binaries | `./verify-prebuilt.sh [--quick] DIR` | released `figram` and `figram-engine` in `DIR`, without Lean |
| Gate self-test | `./verify-faults.sh prebuilt` or `./verify-faults.sh full` | every injected fault makes `./verify-prebuilt.sh` or `./verify.sh` fail |
| Formats | `tests/formats/run_all.sh [LOGDIR [SEEDS]]` | JSON text layer, schemas, engine requests, CLI output, parser budgets |
| Compiler | `tests/compiler/run_all.sh [ENGINE FIGRAM [LOGDIR]]` | an independent oracle of the semantics, differential, metamorphic and mutation tests |
| C ABI | `tests/capi/build.sh`, then `tests/capi/sanitizers.sh` and `tests/capi/mutants.sh`; `tests/capi/build_check.sh` | the shim under ASan/UBSan and TSan; mutants of the shim; the product build of `ffi/build.sh` on this toolchain |
| Go | `cd clients/go && go test ./...` and `go test -race ./...` | the Go wrapper |
| Proof gate regression | `tests/proofs/test_strict_gate_clean.sh` | the strict proof gate never rewrites tracked files |
| Documentation | `python3 ci/check_docs.py` | the documentation against the code (below) |

Run `lake build` and `bash ffi/build.sh` before the suites that need the binaries or the
library. Most suites also run deliberately broken variants of their own oracles (or of the
code under test) and expect those to fail, so a suite that checks nothing is detected.

## `./verify.sh`

`./verify.sh` runs from any directory, writes the full output of the longer steps to
`verify-logs/` in the repository root and prints a summary per step. Exit status: 0 — every step ran and passed; 1 — a failure;
3 — every step that ran passed, but an explicitly allowed deviation was used
(`FIGRAM_WAIVE=go`: Go is not installed and the Go steps were skipped). CI treats 3 as a
failure. On a 4-core machine with a warm Lake cache it takes about 4–5 minutes.

Steps, in order:

1. **Provenance** — date, number of cores, commit, number of modified tracked files, the
   SHA-256 of `verify.sh`, versions of Lean, Python, `jsonschema` and Go.
2. **Build without warnings** — `lake build` must succeed and print no warning.
3. **Reproducibility** (only with `FIGRAM_SHA256SUMS=FILE`) — the stripped `figram` and
   `figram-engine` must match the checksums in `FILE`; the release workflow uses this. Without
   the variable the step is skipped (not a waiver).
4. **Pinned theorems** — `tests/proofs/theorems.lean`: the statements of the 23 pinned theorems
   must equal `tests/proofs/THEOREMS.expected` and depend only on `propext`, `Quot.sound` and
   `Classical.choice`; the Lean sources must not contain `axiom`, `sorry`, `admit`,
   `native_decide`, `implemented_by`, `@[extern`, `skipKernelTC`, `ofReduceBool`,
   `ofReduceNat` ([proofs.md](proofs.md)).
5. **Strict proof gate** — `tests/proofs/strict_gate.sh`: axioms of all 141 source theorems and
   of the whole environment, pinned statements, `leanchecker` ([proofs.md](proofs.md)).
6. **Golden scenarios** — `figram golden` regenerates `examples/golden.jsonl` (12 scenarios) and
   the two example documents; they must equal the committed files byte for byte.
7. **Conformance** — `figram trace` generates 200 random reference scenarios of length 40 for
   each example document and must cover every arrow variant; `tests/engine/conformance.py`
   replays the golden and the random scenarios against `figram-engine` and requires zero
   mismatches. The reference scenarios come from the proved chart semantics, not from the
   engine.
8. **Random documents** — `tests/engine/random_docs.py`: 100 random valid documents
   (`figram random`) with 12 messages each, every one checked on 20 reference scenarios of length 30.
9. **Mutation testing** — `tests/engine/mutation.py` runs every mutant of the compiled machine
   (`figram-engine --mutants`, at least 107 on the two example documents) and requires that the
   scenarios detect each one.
10. **Host contract** — `tests/engine/host_sim.py` ([host-contract.md](host-contract.md),
    section 7).
11. **CLI argument errors** — invalid `figram trace` arguments must give a non-zero exit status.
12. **Huge exponent** — a document with `1e1000000000` must be rejected with exit status 1 in
    under 5 seconds.
13. **Engine memory** — `tests/engine/engine_memory.py`: the engine starts under `ulimit -v` 32,
    64 and 256 MiB, reserves at most 64 MiB of address space, uses at most 16 MiB after 2000
    requests, runs one thread, and aborts with Lean's stack overflow message on a tiny stack.
14. **Snapshots and protocol edges** — `tests/engine/snapshot_fixtures.py` (at least 24
    snapshots: `schemas/snapshot.schema.json` and the engine accept and reject the same ones;
    the `seq` boundary) and `tests/engine/protocol_edges.py` (at least 106 hand-written cases:
    error codes, strict fields, tokens and `seq` by value, `start` with `seq`, `restart`, `id`
    echo, invalid UTF-8, blank lines, the size limit, reply order, the mutant gating).
15. **JSON text layer** — `tests/engine/json_fuzz.py`: 30 000 random inputs, `figram json`
    against Python `json` with the format's restrictions (same accept/reject decision, same
    value, printing reads back). A deliberately lax parser must be detected (sensitivity
    control).
16. **C shim** — `bash ffi/build.sh`; `tests/capi/c_harness.c` with the shim built under ASan
    and UBSan: invalid UTF-8, a 5 MB request, 16 threads calling one machine; then, in a plain
    build, 5000 loads and unloads must grow memory by less than 2 MiB, while a control run without
    unloading must grow it by more than 8 MiB.
17. **C ABI** — `tests/capi/regress.sh`, at least 44 checks, each in its own process:
    initialization under `RLIMIT_AS`, concurrent and lazy initialization (300 processes each),
    `NULL` arguments, foreign and unloaded handles, NUL bytes, strict UTF-8, the size limit,
    signal dispositions and `sigaltstack`, no threads or file descriptors from initialization,
    a malloc failure, edge cases that must not crash the host (init twice, calls before init,
    double unload, a request after unload), 64 threads on one handle, a 16-thread stress test;
    `ffi/figram.h` must compile as C99 and as C++ without warnings.
18. **Runtime without the libuv event loop** — see "Reachability" below.
19. **Python client** — `tests/engine/client_faults.py`, `tests/engine/client_contract.py`,
    `tests/engine/client_robustness.py`, each with an empty stderr
    ([host-contract.md](host-contract.md), section 7).
20. **Harness self-test** — `tests/engine/harness_selftest.py`: corrupted, incomplete, empty and
    hanging fake engines must make the conformance, load and snapshot tests fail (at least 37
    checks).
21. **Load test** — `tests/engine/loadtest.py`: 64 Python threads over 2 engine processes,
    every step compared with the reference.
22. **Go** — `clients/go`: the `gofig` driver (built with `--gc-sections`) and its `-race` build
    replay the scenarios from hundreds of goroutines sharing loaded machines; zero rounds or bots
    and 7 kinds of corrupted replies must fail; 5000 loads with `Close` must grow memory by less than
    4 MiB (a control run without `Close`: more than 8 MiB);
    selected `go test -race` tests (a request map shared by goroutines, the wrapper's error
    handling) and `TestFixInitUnderRlimitAS` (the runtime starts under `RLIMIT_AS`); a corrupted
    scenario file must fail.

`./verify-faults.sh full` checks the gate itself: every injected fault (a truncated golden file,
tampered checksums, a custom axiom, `native_decide`, a weakened theorem, a build warning, a
missing Go, …) must make `./verify.sh` fail, each in its own copy of the tree.
`./verify-faults.sh prebuilt` does the same for `./verify-prebuilt.sh` (truncated or shrunk
fixtures, swapped binaries). Logs go to `verify-logs/faults/` (`FAULT_LOGS` overrides it).

`./verify-prebuilt.sh DIR` runs the engine, CLI, client, harness, host-contract and text-layer
steps of `./verify.sh` on the binaries in `DIR` without Lean (about 3 minutes); it does not
cover the build, the proofs, the C ABI and Go. Exit status: 0 — passed; 1 — a failure;
3 — the shortened `--quick` check passed, which does not count as a pass; 2 — a usage error.

## Other suites

**Formats** (`tests/formats/run_all.sh [LOGDIR [SEEDS]]`, about 1.5 minutes): round trips of
generated documents through `figram fmt`/`check`, an RFC 8259 table and random inputs against
the text parser, a table of documents and snapshots against both JSON schemas, engine requests,
control bytes in CLI diagnostics, and time and memory budgets of the parser on large inputs.
Most tests also run with a deliberately broken oracle or a mutant `figram`, which must fail.
`FIGRAM` and `FIGRAM_ENGINE` name the binaries under test (default: `.lake/build/bin`); the logs
go to `LOGDIR` (default `tests/formats/logs`).

**Compiler** (`tests/compiler/run_all.sh [ENGINE FIGRAM [LOGDIR]]`, about 8 minutes): an
independent oracle written from the prose of [format.md](format.md) and
[host-contract.md](host-contract.md) (not from `Figram/Chart.lean`), directed and random
differential tests, document upgrades, mutants, metamorphic tests, and red runs of the oracle
itself with deliberately wrong readings.
`tests/compiler/perf_scale.py` measures load and step time on large documents (below).

**C ABI** (`tests/capi/build.sh`, `tests/capi/sanitizers.sh`, `tests/capi/mutants.sh`, under a
minute): the regression subtests built with ASan+UBSan and with TSan, and a mutant per fix
of the shim (each fix reverted in a copy of `ffi/figram_ffi.c` or by replacing
`ffi/figram_init.cpp`) that the corresponding subtest must catch. ASan, valgrind and TSan do not
see the Lean heap (mimalloc); the functional stress test `tests/capi/stress.c` that checks every
reply is the working oracle for concurrency ([embedding.md](embedding.md)).
`tests/capi/build_check.sh` checks the product build itself: `ffi/build.sh` on this toolchain,
a C host linked with only the flags of `ffi/out/LDFLAGS`, and a plain `go build` of
`clients/go`.

**Go** (`cd clients/go && go test ./...`, `go test -race ./...`): the wrapper's regression
tests, including the protocol checks of `checkReply`, lazy initialization, `RLIMIT_AS` and
concurrent use.

## Continuous integration

[`.github/workflows/ci.yml`](../.github/workflows/ci.yml) runs on every push and pull request,
with the official Lean release of `lean-toolchain` (installed by `ci/install-lean.sh`), Go 1.23
and Python `jsonschema` on `ubuntu-latest`:

* **verify.sh** — `./verify.sh` (exit status 0 only), then a long parallel run of the Go driver
  (`gofig` and `gofig-race` on 2000 scenarios from all cores);
* **suite formats**, **suite compiler**, **suite capi** (`tests/capi/build.sh`,
  `sanitizers.sh`, `mutants.sh`, `build_check.sh`), **suite go** (`gofmt`, `go vet`,
  `go test` plain and `-race`), **suite proofs** (`tests/proofs/test_strict_gate_clean.sh` and
  the proof controls of [proofs.md](proofs.md)),
  **suite docs** (`python3 ci/check_docs.py`), **suite faults-prebuilt** and
  **suite faults-full** (`./verify-faults.sh`);
* **release dry run** — packages the commit with `ci/package.sh`, repackages a clean rebuild and
  requires byte-identical archives, runs `ci/test-bundle.sh` and `./verify-prebuilt.sh` on the
  extracted archives ([releasing.md](releasing.md)).

The verify and suite jobs upload their logs as workflow artifacts; the release dry run uploads
its logs when it fails.

## Reachability

`figram-engine`, `figram` and the embedded library start the Lean runtime without the libuv
event loop ([embedding.md](embedding.md)). `./verify.sh` checks this on the built files, not on
the source:

* the calls made by `lean_initialize_runtime_module` in each built file are compared, by
  disassembly, with the stock function from the installed `libleanrt.a`: they must be the stock
  steps without `initialize_libuv` (executables) and also without `initialize_stack_overflow`
  (library). A new start-up step in another Lean version stops the check;
* [`ci/reach.py`](../ci/reach.py) builds a reference graph of the ELF file and requires the
  event-loop functions (`lean_uv_*`, `event_loop_*`, `initialize_libuv*`,
  `g_uv_*_external_class`) to be unreachable in `figram`, `figram-engine` and a C host linked
  with `libfigram.a`, and the stack-overflow handler to be reachable in the executables and
  unreachable in the library host.

What `ci/reach.py` proves: nodes are the symbols of the file (functions and data objects);
edges are every address referenced by the code (`call`, `jmp`, `lea`, `mov`, … in
`objdump -d`) and every pointer stored in data (in a PIE every such pointer has a dynamic
relocation, `R_X86_64_RELATIVE` or `IRELATIVE`); roots are the entry point, `main`, `_init`,
`_fini`, the `.preinit_array`, `.init_array` and `.fini_array` entries, the dynamic symbols and
pointer targets stored outside known symbols. The result over-approximates reachability: an
unreachable symbol is never executed or read by any path, except through addresses computed at
run time from something other than the file's code and data (such as `dlsym`). A file that is
not a PIE is refused (exit status 2), because its data pointers would be invisible. The check is
calibrated twice: a synthetic program with functions reached only through a pointer table, a
constructor and a destructor (reachable) and one dead function (unreachable), and the same C host
linked with the stock runtime start-up, in which the event-loop functions must be reachable.

## Documentation check

`python3 ci/check_docs.py` (Python 3, standard library only) checks the documentation against
the repository and exits with status 0 when they agree:

* every relative link and every repository path mentioned in the Markdown files at the root and
  in `docs/` exists;
* the theorems listed in [proofs.md](proofs.md) equal the pinned list in
  `tests/proofs/THEOREMS.expected`, and every theorem name mentioned in the documentation exists
  in the Lean sources;
* the error codes in [format.md](format.md) equal those produced by the engine
  (`ErrCode.name` in `Figram/Engine.lean`), the C shim and the protocol validators, and every
  request field of every operation is documented;
* the CLI commands documented in the README equal the commands of the usage text in `Main.lean`;
* the functions documented in [embedding.md](embedding.md) equal those declared in
  `ffi/figram.h`, which equal those exported by `ffi/figram_ffi.c`;
* every runnable example (fenced blocks marked `sh doctest`, `c doctest`, `python doctest`) runs
  and prints the output shown, with `...` standing for any text;
* the numbers the documentation states about the tests (pinned theorems, golden scenarios,
  minimum numbers of mutants and checks) equal their sources;
* references to sections (`section N`, `section N, decision M`, a quoted heading) point at
  sections, decisions and headings that exist;
* the numbered steps of `./verify.sh` above match `verify.sh`, every job and suite of `ci.yml` is
  described in "Continuous integration", and every job of `release.yml` in
  [releasing.md](releasing.md);
* the `lake build` command of `ffi/build.sh` and the documents of each release archive are the
  ones stated in [embedding.md](embedding.md) and [releasing.md](releasing.md);
* `LICENSE` is the MIT license of the Figram contributors, and the license texts in
  `THIRD_PARTY_NOTICES.md` are complete (their hashes are pinned);
* no Cyrillic text remains in the Markdown files at the root and in `docs/`.

Options: `--bin DIR` (directory with `figram` and `figram-engine`, default `.lake/build/bin`),
`--no-run` (skip the runnable examples), `--repo DIR` (repository root).

## Performance

Measured on a shared 4-vCPU KVM machine (load average 3–5 during the runs), so the numbers show
orders of magnitude, not guarantees. None of them is part of `./verify.sh`.

**Throughput of one engine process** — 200 000 `step` requests on `examples/pizza.bot.json`
streamed from a file:

```sh
python3 -c 'for i in range(200000): print("{\"id\":%d,\"op\":\"step\",\"snapshot\":{\"at\":\"menu\",\"seq\":1},\"event\":\"btn:order\"}" % i)' > /tmp/requests.jsonl
time .lake/build/bin/figram-engine examples/pizza.bot.json < /tmp/requests.jsonl > /dev/null
```

| Build | CPU time (user + sys), three runs | Per request |
|---|---|---|
| official Lean 4.35.0 release | 1.82–2.07 s | about 10 µs |
| Lean 4.35.0 built from source (gcc) | 2.22–2.43 s | about 11–12 µs |

**Large documents** — `tests/compiler/perf_scale.py`, local source build: load is the CPU time
of `figram-engine DOC` answering one `hello` (parse, check, compile, `wf`); the worst step is the
largest mean CPU time per `step` request among the scenario's events.

```sh
python3 tests/compiler/perf_scale.py --engine .lake/build/bin/figram-engine --figram .lake/build/bin/figram \
  --only states1000,states3000,unique2000,hist_200x20,global_big,flat_4000 --check
```

| Scenario | Messages | Load, CPU | `figram check`, CPU | Worst step, CPU |
|---|---|---|---|---|
| `states1000` — shared events, two variants per button (one guarded), sections of 10 messages | 1000 | 0.03 s | 0.03 s | 72 µs |
| `states3000` — the same with 3000 messages | 3000 | 0.07 s | 0.08 s | 255 µs |
| `unique2000` — a button of its own on every message, timers on 10 % of messages | 2000 | 0.18 s | 0.19 s | 118 µs |
| `hist_200x20` — 200 sections × 20 messages with "resume" into every section | 4001 | 0.06 s | 0.08 s | 234 µs |
| `global_big` — 1000 global commands, about 10^6 machine rows | 1000 | 0.37 s | 0.38 s | 124 µs |
| `flat_4000` — one section, three buttons per message | 4000 | 0.16 s | 0.17 s | 435 µs |

A step walks the rows of the current message and the variants bubbling up from its sections,
so its cost grows with the document; load time depends on the shape of the document as much as
on its size, because shared and global events multiply by the number of messages.

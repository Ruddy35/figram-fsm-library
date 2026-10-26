# Embedding the engine (C ABI, Go)

Besides the `figram-engine` process, the engine is available as a static library with a C ABI,
`libfigram.a`, for hosts written in C, C++ or Go (cgo). It runs the same compiled Lean code as
the process and speaks the same protocol, one request per call ([format.md](format.md),
section 8). The contract is the header [`ffi/figram.h`](../ffi/figram.h); this page explains it.

## Building and linking

```sh
bash ffi/build.sh
```

`ffi/build.sh` runs `lake build Figram:static FigramApi:static` and writes:

| Output | Contents |
|---|---|
| `ffi/out/libfigram.a` | the compiled Figram modules that the engine needs (`Core`, `Chart`, `Compile`, `Lint`, `Format`, `FormatIO`, `Engine`, `Api`) and the C shim (`ffi/figram_ffi.c`, `figram_guard.cpp`, `figram_rt.c`, `figram_init.cpp`) |
| `ffi/out/lean/` | the static archives of the Lean runtime and of its dependencies (see below) |
| `ffi/out/LDFLAGS` | linker flags for a host (absolute paths) |
| `ffi/out/LDFLAGS.runtime` | the same flags without `libfigram.a` |
| `clients/go/zz_figram_ldflags.go` | the same flags as a `#cgo` directive for the reference Go wrapper (generated, not in git) |

The library is therefore `libfigram.a` *plus* the runtime archives in `ffi/out/lean/`, not a
single file. `ffi/build.sh` supports both layouts of a Lean 4.35.0 toolchain and detects them
by the presence of `$PREFIX/lib/libc++.a`:

| Lean toolchain | Archives in `ffi/out/lean/` | Dynamic dependencies of a C host |
|---|---|---|
| official release (clang, libc++) | `libInit.a`, `libleanrt.a`, `libc++.a`, `libc++abi.a`, `libuv.a`, `libgmp.a` from the Lean prefix | `libc`, `libm`, `libgcc_s.so.1` |
| built from source (gcc, libstdc++) | `libInit.a`, `libleanrt.a` and the system's static `libuv.a`, `libgmp.a` (Debian/Ubuntu: `apt install libuv1-dev libgmp-dev`) | `libc`, `libm`, `libstdc++.so.6`, `libgcc_s.so.1` |

The last column is what `readelf -d` lists for the example host below, built with gcc 13 on
Ubuntu 24.04 against each layout.

Link a C or C++ host:

```sh
cc host.c -I ffi $(cat ffi/out/LDFLAGS)
```

The release archive `libfigram-<version>-linux-x86_64.tar.gz` contains the header, `libfigram.a`
and the runtime archives, built with the official toolchain; [releasing.md](releasing.md)
describes its layout and how to link against it.

A minimal host that loads [`examples/pizza.bot.json`](../examples/pizza.bot.json) and sends two
requests:

```c doctest args=examples/pizza.bot.json
#include <stdio.h>
#include <stdlib.h>
#include "figram.h"

int main(int argc, char** argv) {
  if (argc != 2) { fprintf(stderr, "usage: host bot.json\n"); return 2; }
  FILE* f = fopen(argv[1], "rb");
  if (!f) { perror(argv[1]); return 2; }
  static char doc[1 << 20];
  size_t n = fread(doc, 1, sizeof doc - 1, f);
  fclose(f);
  doc[n] = 0;

  char* err = NULL;
  void* bot = figram_load_doc_ex(doc, &err);   /* the first call initializes the runtime */
  if (!bot) { fprintf(stderr, "load failed: %s\n", err); figram_free(err); return 1; }

  const char* reqs[] = {
    "{\"id\":1,\"op\":\"start\"}",
    "{\"id\":2,\"op\":\"step\",\"snapshot\":{\"at\":\"menu\",\"seq\":1},\"event\":\"btn:order\"}",
  };
  for (int i = 0; i < 2; i++) {
    char* reply = figram_request_json(bot, reqs[i]);
    if (!reply) return 1;                       /* NULL only when out of memory */
    printf("%s\n", reply);
    figram_free(reply);
  }
  figram_unload(bot);
  return 0;
}
```

```text output
{"id":1,"snapshot":{"at":"menu","seq":1},"actions":[{"send":"menu"}]}
{"id":2,"snapshot":{"at":"catalog","back":"menu","resume":{"order":"catalog"},"seq":2},"actions":[{"send":"catalog"}]}
```

**Static linking only.** `libleanrt.a` is compiled with `-ftls-model=local-exec`, so the
library cannot be linked into a shared object (`.so`, Go `-buildmode=c-shared`, plugins): the
linker stops with `relocation R_X86_64_TPOFF32 … can not be used when making a shared object`.
Link it into an executable.

**`--gc-sections`.** `ffi/out/LDFLAGS` contains `-Wl,--gc-sections`, as Lean uses for its own
executables: only the reachable code of the Lean runtime and core library ends up in the
program. cgo does not accept this flag in a `#cgo` directive, so the generated
`zz_figram_ldflags.go` omits it; pass it when building:

```sh
cd clients/go
go build -ldflags=-extldflags=-Wl,--gc-sections
```

Measured on the reference Go driver (`gofig`, local source-built toolchain, Go 1.24): 16.6 MB
without the flag, 6.8 MB with it (11.0 MB and 4.5 MB after `strip`), using
`go build -o gofig .` and `go build -ldflags=-extldflags=-Wl,--gc-sections -o gofig .` in
`clients/go`.

## The C ABI contract

Functions (all declared in [`ffi/figram.h`](../ffi/figram.h)):

| Function | Purpose |
|---|---|
| `figram_init` | initialize the runtime (optional; every other function does it on first use) |
| `figram_last_error` | text of the last error detected in the calling thread |
| `figram_set_max_request` | request size limit in bytes for the whole process (0 — none) |
| `figram_check_json`, `figram_check_json_n` | check a document: `"ok"` or the errors separated by newlines (the warnings of `figram check` are not reported) |
| `figram_fmt_json`, `figram_fmt_json_n` | canonical form of a document, or `"error: …"` |
| `figram_load_doc`, `figram_load_doc_n`, `figram_load_doc_ex`, `figram_load_doc_ex_n` | load a document and return a machine handle (or NULL) |
| `figram_request_json`, `figram_request_json_n` | one protocol request to a loaded machine; the reply is one JSON line |
| `figram_unload` | free a machine |
| `figram_free` | free a string returned by the library |

**Initialization.** The first call of any function initializes the Lean runtime — thread-safe
and exactly once; concurrent first calls wait for the first one. `figram_init()` can be called
explicitly to learn the result early: 0 — ready; 1 — the initialization of the Lean module
returned an error; 2 — the Lean runtime did not start (a C++ exception during start-up, caught by
the shim). A failed initialization is final: every function then reports an error (the reason
is in `figram_last_error()`), and the host process stays alive. There is no `figram_shutdown`.

**Ownership.** Every returned `char*` belongs to the caller and is freed with `figram_free`
(`NULL` is allowed). A function returns `NULL` instead of a string only when memory runs out.
Input strings are only read and are not used after the call returns. A machine handle belongs
to the caller and is freed with `figram_unload`. `figram_last_error()` returns a string owned by
the library, valid until the next call in the same thread; successful calls do not reset it
(like `errno`).

**Strings.** The variants without `_n` take a C string (up to the first NUL byte). The `_n`
variants take a buffer and a length (no terminating NUL needed) and **reject** an embedded NUL
byte. Input must be strictly valid UTF-8 (no overlong forms, surrogates or code points above
U+10FFFF); invalid input is an error, never replaced with U+FFFD. `figram_load_doc_ex(doc, &err)`
writes the reason of a failed load into `*err` (free it with `figram_free`; `*err` is `NULL` on
success; `err` may be `NULL`).

**Request errors detected by the shim** produce a protocol error reply
`{"id":null,"error":"<text>","code":"<code>"}`:

* `bad_request` — `NULL` instead of the handle or the request, a pointer that is not a handle,
  a machine that was already unloaded;
* `parse_error` — an empty request (the `figram-engine` process skips empty lines instead — a
  documented asymmetry), a NUL byte (`_n` variants), invalid UTF-8;
* `too_large` — a request longer than the `figram_set_max_request` limit (1 MiB = 1 048 576
  bytes by default, like `figram-engine --max-request`).

In these replies `id` is always `null` (the process echoes the `id` from the start of the line):
the reply belongs to this call anyway. All other errors (JSON, fields, snapshot, codes, the `id`
echo) come from the engine itself, exactly as in the `figram-engine` process.

**Handles.** `NULL` instead of an argument, a foreign pointer instead of a handle, a request to
an unloaded machine and a repeated `figram_unload` are reported as errors, not as a crash. An
unloaded handle is detected while it is among the last 1024 unloads (the slot is quarantined
until then). `figram_unload(NULL)` does nothing, like `free(NULL)`.

**Undefined behaviour** (not detected):

* `figram_unload` concurrently with requests to the **same** handle (serialize them yourself;
  the Go wrapper uses a `sync.RWMutex`);
* a request or unload through a handle unloaded more than 1024 unloads ago: its slot may belong
  to a new machine by then;
* a string without a terminating NUL in the variants without `_n`; a buffer shorter than `len`
  in the `_n` variants; modifying an input buffer during a call;
* `figram_free` on a pointer that was not returned by the library (as with `free`);
* calls in a child process after `fork()` (not tested).

**Threads.** All functions may be called from any threads concurrently, including with one
shared handle: a loaded machine is marked multi-threaded (`lean_mark_mt`), so its reference
counts are atomic. Because of these atomic counters, a C host with several worker threads may
prefer one handle per thread to avoid contention on shared cache lines.

## Side effects of initialization

The Lean runtime starts **without the libuv event loop and without the stack overflow
handler**:

* `figram_init` creates no threads (neither the libuv loop thread nor an io_uring kernel
  thread), no file descriptors and no signal handlers, and does not touch `sigaltstack`;
* it reserves less than 1 MiB of address space (measured: +768 KiB of `VmSize` with a Lean built
  from source, +832 KiB with the official release, by the subtest `host_state` of
  `tests/capi/contract.c`, which fails above 8 MiB);
* the only change to the process is the disposition of `SIGPIPE`: the runtime sets it to
  `SIG_IGN`, and the shim saves and restores it. If another host thread changes `SIGPIPE`
  concurrently with the initialization, that change may be lost;
* a stack overflow in Lean code is an ordinary `SIGSEGV` of the host process: signals belong to
  the host.

How this works: the stock `lean_initialize_runtime_module` of Lean 4.35 starts libuv timers,
sockets, signals and DNS together with a permanent event-loop thread. Figram uses none of them,
so [`ffi/figram_init.cpp`](../ffi/figram_init.cpp) defines its own
`lean_initialize_runtime_module` with the same steps except `initialize_libuv` (and, for the
library, `initialize_stack_overflow`); the linker takes it from `libfigram.a` before the object
in `libleanrt.a`. `figram_init.cpp` refuses to compile with a Lean version other than 4.35.
`./verify.sh` checks the built files: the start-up steps are compared, by disassembly, with the
stock ones from the installed `libleanrt.a` (a new step in another Lean version stops the check),
and [`ci/reach.py`](../ci/reach.py) proves the event-loop functions unreachable
([testing.md](testing.md), "Reachability").

The executables `figram` and `figram-engine` keep Lean's stack overflow handler: a stack
overflow aborts them with Lean's message `Stack overflow detected. Aborting.`

## Memory and RLIMIT_AS

By default the Lean runtime reserves 1 GiB of address space for the arena of its allocator
(mimalloc) and 1 GiB for the stack of each runtime thread. Before the runtime starts,
`figram_init` and the executables disable the arena reservation (memory is taken from the OS as
needed) and set the runtime thread stack size to that of any thread of the process
(`RLIMIT_STACK`, `ulimit -s`) — [`ffi/figram_rt.c`](../ffi/figram_rt.c). Explicitly set
`MIMALLOC_ARENA_RESERVE` and `LEAN_STACK_SIZE_KB` take precedence.

The embedded runtime therefore starts under `ulimit -v` / systemd `LimitAS=` with little
headroom: the C ABI tests initialize it with 2 MiB to 1 GiB of address space above the current
usage and under `prlimit --as=512000000`. With 256 KiB of headroom the Lean allocator terminates
the process with `INTERNAL PANIC: out of memory` and exit status 1 — the same thing happens when
memory runs out later, for example on a huge request. This is not caught; choose the limit
with a margin.

The executables also run `main` on the main thread of the process (`LEAN_MAIN_USE_THREAD=0`,
[`ffi/figram_exe.c`](../ffi/figram_exe.c)), so `figram-engine` is single-threaded. Measured
with `tests/engine/engine_memory.py` after 2000 requests on `examples/pizza.bot.json`:

| Build | Address space (`VmSize`) | Resident (`VmRSS`) | Threads |
|---|---|---|---|
| official Lean 4.35.0 release | 10 MiB | 4–5 MiB | 1 |
| Lean 4.35.0 built from source (gcc) | 12 MiB | 6 MiB | 1 |

```sh
python3 tests/engine/engine_memory.py .lake/build/bin/figram-engine examples/pizza.bot.json
```

The same script checks that the engine answers under `ulimit -v` 32, 64 and 256 MiB, and that
with a 24 or 32 KiB stack a document nested 250 levels deep aborts it with Lean's stack overflow
message. Recursion depth of Lean code is bounded by the stack of the calling thread (for Go
threads, the system thread stack size): a request or a document nested 256 levels deep (the
limit of the parser) needs up to about 68 KiB of stack in the calling thread — nested objects,
such as a document with 126 nested sections, about 68 KiB, nested arrays about 56 KiB — and
crashes the host process with `SIGSEGV` on a thread with 64 KiB (measured with a Lean built from
source); give threads that call the library at least 128 KiB. `tests/capi/regress.sh` makes
such calls on a thread of the size recommended here.

## Go

For Go programs use the module [`github.com/Ruddy35/ffl-go`](https://github.com/Ruddy35/ffl-go)
(`go get github.com/Ruddy35/ffl-go@v0.2.0`): a typed API (`Load`, `Start`, `StartWith`, `Step`,
`Timer`, `Restart`, `Decide`, `Check`, `Fmt`) over this C ABI that bundles the static libraries
of the release for linux/amd64, so no build of this repository is needed. A version of the
module bundles one release and speaks its protocol: v0.2.0 bundles this release (protocol 2),
and its `Load` rejects a library of another protocol version, which `-tags figram_system` could
link. v0.1.0 speaks protocol 1 and works only with release 0.1.0: it has no `start` with an
event and rejects every reply whose snapshot carries `back` ("unknown field").

The reference wrapper used by the tests of this repository lives in
[`clients/go`](../clients/go). It is a cgo wrapper (`Init`, `SetMaxRequest`, `Load`,
`Bot.Request`, `Bot.Close`) together with the `gofig` test driver that replays reference
scenarios from many goroutines. Both are one `main` package (module `gofig`).

* `Load(doc)` initializes the runtime on first use (`sync.Once`) and returns a `*Bot`, or an
  error with the reason of the failed load (`figram_load_doc_ex_n`).
* `Bot.Request(map)` sends one request with a fresh numeric `id`, decodes the reply with
  `json.Number` and validates it strictly (`checkReply`: the `id`, exactly one reply variant,
  field types, known error `code`). The caller's map is not modified, so one map may be shared
  between goroutines.
* A `*Bot` is safe for concurrent `Request` calls; `Close` frees the machine after waiting for
  running calls (`sync.RWMutex`), after which `Request` returns an error. Documents can be
  reloaded without memory growth (`gofig -reload N` checks this).
* `figram_last_error()` is thread-local; the wrapper reads it in the same C call, because a
  goroutine may move to another OS thread between two cgo calls.

To use the wrapper in your own module, pass the directory of `figram.h` and the flags from
`ffi/out/LDFLAGS` in the environment (its `#cgo CFLAGS` line points into this repository; cgo
accepts `-Wl,--gc-sections` from the environment):

```sh
CGO_CFLAGS="-I/path/to/figram/ffi" CGO_LDFLAGS="$(cat /path/to/figram/ffi/out/LDFLAGS)" go build
```

In this repository `clients/go` builds with plain `go build` thanks to the generated
`zz_figram_ldflags.go` (run `bash ffi/build.sh` first).

## Testing the library

See [testing.md](testing.md): the C ABI regression suite (each subtest in its own process,
including initialization under `RLIMIT_AS` and 8 threads × 300 processes of concurrent lazy
initialization), the sanitizer variants and the mutants of the shim, and the Go tests (plain and
`-race`). ASan, valgrind and TSan do not see the Lean heap (mimalloc): use-after-free of Lean
objects and a missing `lean_mark_mt` are invisible to them, and concurrent loads produce false
"races" under TSan. The working oracle is a functional stress test that checks every reply
(`tests/capi/stress.c`: 16 threads × 20 000 requests while other machines are loaded and
unloaded).

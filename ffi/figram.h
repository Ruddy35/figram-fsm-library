/* figram.h — C ABI of the embeddable Figram engine (libfigram.a, built by ffi/build.sh).
 *
 * JSON strings in and out; the request protocol is version 2 of docs/format.md, section 8.
 * Build: ffi/build.sh → ffi/out/libfigram.a, linker flags in ffi/out/LDFLAGS; with F the
 * repository root:
 *     cc host.c -I "$F/ffi" $(cat "$F/ffi/out/LDFLAGS")
 *     CGO_CFLAGS="-I$F/ffi" CGO_LDFLAGS="$(cat "$F/ffi/out/LDFLAGS")" go build
 * Only static linking into an executable is supported: libleanrt.a is compiled with
 * -ftls-model=local-exec, so a shared object (.so, Go -buildmode=c-shared, plugins) cannot be
 * built (R_X86_64_TPOFF32). The guide is docs/embedding.md.
 *
 * INITIALIZATION. The first call of any function initializes the Lean runtime (thread-safe,
 * exactly once; concurrent first calls wait for the first one). figram_init() can be called
 * explicitly to learn the result early. A failed initialization is final: every function then
 * reports an error (the text is in figram_last_error()), and the host process stays alive.
 *
 * OWNERSHIP. Every returned char* belongs to the caller and is freed with figram_free (NULL is
 * allowed). A function returns NULL instead of a string only when memory runs out.
 * Input strings are only read and are not used after the call returns.
 *
 * STRINGS. The variants without _n take a C string (up to the first NUL byte). The _n variants
 * take a buffer and a length (no terminating NUL needed) and REJECT an embedded NUL byte. Input
 * must be strictly valid UTF-8 (no overlong forms, surrogates or code points above U+10FFFF);
 * invalid input is an error, never replaced with U+FFFD.
 *
 * THREADS. All functions may be called from any threads concurrently, including with one shared
 * handle (a loaded machine is marked with lean_mark_mt: its reference counts are atomic). A C host
 * with several worker threads may prefer one handle per thread, so that the atomic counters of a
 * shared handle do not contend on shared cache lines.
 *
 * SIDE EFFECTS OF INITIALIZATION (there is no figram_shutdown). The Lean runtime starts without the
 *   libuv event loop and without the stack overflow handler (ffi/figram_init.cpp): figram_init
 *   creates no threads (neither the libuv loop thread nor an io_uring kernel thread), no file
 *   descriptors and no signal handlers, does not touch sigaltstack, and reserves less than 1 MiB
 *   of address space (measured; the C ABI tests allow at most 8 MiB). The only change to the
 *   process is the disposition of SIGPIPE (the runtime sets it to SIG_IGN): the shim saves and
 *   restores it; if another host thread changes SIGPIPE concurrently with the initialization,
 *   that change may be lost. A stack overflow in Lean code is an ordinary SIGSEGV of the host
 *   process.
 *
 * MEMORY AND RLIMIT_AS. By default the mimalloc allocator of the Lean runtime reserves a 1 GiB
 *   arena of address space; before the runtime starts, figram_init disables the reservation
 *   (ffi/figram_rt.c: memory is taken from the OS as needed; an explicit MIMALLOC_ARENA_RESERVE
 *   takes precedence). The runtime therefore starts under RLIMIT_AS (ulimit -v, systemd LimitAS=)
 *   with little headroom (the tests start it with 2 MiB to 1 GiB of address space above the
 *   current usage).
 *   figram_init code 2 is a C++ exception of the runtime during start-up, caught by the shim
 *   (Lean 4.35 has no known cause for it). The recursion depth of Lean code is bounded by the
 *   stack of the calling thread (for Go threads, the system thread stack size).
 *   Not caught: when memory runs out (too little headroom at start-up, such as 256 KiB, or a huge
 *   request later), the Lean allocator terminates the process with "INTERNAL PANIC: out of
 *   memory" and exit status 1; choose the limit with a margin.
 *
 * REQUEST ERRORS detected by the shim are protocol error replies
 *   {"id":null,"error":"<text>","code":"<code>"}:
 *   bad_request — NULL instead of the handle or the request, a pointer that is not a handle, an
 *                 unloaded machine;
 *   parse_error — an empty request (the figram-engine process skips empty lines instead: a
 *                 documented asymmetry), a NUL byte (_n variants), invalid UTF-8;
 *   too_large   — a request longer than the figram_set_max_request limit (1 MiB by default).
 *   The too_large and invalid UTF-8 texts are the same as those of figram-engine. Unlike the
 *   process, id is always null in these replies (the process echoes the id from the start of the
 *   line): the reply belongs to this call anyway. All other errors (JSON, fields, snapshot, codes,
 *   the id echo) come from the engine, exactly as in the figram-engine process.
 *
 * UNDEFINED BEHAVIOUR (not detected):
 *   - figram_unload concurrently with requests to the SAME handle (serialize them yourself;
 *     the Go wrapper uses a RWMutex);
 *   - a request or unload through a handle unloaded more than 1024 unloads ago: its slot may
 *     belong to a new machine by then, and the request is answered by that machine;
 *   - a string without a terminating NUL in the variants without _n; a buffer shorter than len
 *     in the _n variants; modifying an input buffer during a call;
 *   - figram_free on a pointer that was not returned by the library (as with free);
 *   - calls in a child process after fork() (not tested).
 *   Note: ASan, valgrind and TSan do not see the Lean heap (mimalloc) and do not replace the
 *   functional stress test that checks every reply (tests/capi/stress.c).
 */
#ifndef FIGRAM_H
#define FIGRAM_H

#include <stddef.h>

#if defined(__GNUC__) || defined(__clang__)
#define FIGRAM_EXPORT __attribute__((visibility("default")))
#else
#define FIGRAM_EXPORT
#endif

#ifdef __cplusplus
extern "C" {
#endif

/* Initialization (optional: the first call of any function performs it).
   0 — ready; 1 — the initialization of the Lean module returned an error; 2 — the Lean runtime
   did not start (a C++ exception during start-up). Repeated calls return the same code.
   On failure the reason is in figram_last_error(). */
FIGRAM_EXPORT int figram_init(void);

/* Text of the last error detected by the library in THIS thread (a failed init, a failed
   document load, a request error detected by the shim, an invalid handle in figram_unload), or
   NULL if there was none. Successful calls do not reset it (like errno). The string belongs to
   the library and is valid until the next call in this thread. */
FIGRAM_EXPORT const char* figram_last_error(void);

/* Request size limit in bytes for figram_request_json*; 0 — no limit.
   The default is 1048576 (like figram-engine --max-request). Returns the previous limit.
   Applies to the whole process. */
FIGRAM_EXPORT size_t figram_set_max_request(size_t max_bytes);

/* Check a document: "ok" or the errors separated by newlines ("error: …" for a parse error,
   NULL, a NUL byte, invalid UTF-8 or a failed init). A raw NUL from a key name is written
   as \u0000. */
FIGRAM_EXPORT char* figram_check_json(const char* doc);
FIGRAM_EXPORT char* figram_check_json_n(const char* doc, size_t len);

/* Canonical form of a document, or "error: …". */
FIGRAM_EXPORT char* figram_fmt_json(const char* doc);
FIGRAM_EXPORT char* figram_fmt_json_n(const char* doc, size_t len);

/* Load a document: a machine handle (owned by the caller, freed with figram_unload) or NULL.
   The _ex variants write the reason of a failure into *err (free it with figram_free; *err is
   NULL on success; err may be NULL); the reason is also in figram_last_error(). */
FIGRAM_EXPORT void* figram_load_doc(const char* doc);
FIGRAM_EXPORT void* figram_load_doc_n(const char* doc, size_t len);
FIGRAM_EXPORT void* figram_load_doc_ex(const char* doc, char** err);
FIGRAM_EXPORT void* figram_load_doc_ex_n(const char* doc, size_t len, char** err);

/* One engine protocol request (docs/format.md, section 8) to a loaded machine; the reply is
   one JSON line. The caller's handle stays valid. */
FIGRAM_EXPORT char* figram_request_json(void* machine, const char* line);
FIGRAM_EXPORT char* figram_request_json_n(void* machine, const char* line, size_t len);

/* Free a machine. NULL does nothing (like free(NULL)). A foreign pointer and a repeated
   unload are detected (nothing is done; the reason is in figram_last_error()). */
FIGRAM_EXPORT void figram_unload(void* machine);

/* Free a string returned by the library. */
FIGRAM_EXPORT void figram_free(char* s);

#ifdef __cplusplus
}
#endif
#endif /* FIGRAM_H */

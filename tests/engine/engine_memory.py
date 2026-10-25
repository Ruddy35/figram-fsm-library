"""Memory of figram-engine: start under RLIMIT_AS (`ulimit -v`) and the address space
reserved after start-up.

    python3 engine_memory.py ENGINE DOCUMENT

By default the Lean runtime reserves 1 GiB for the stack of each of its threads and 1 GiB for
the mimalloc arena (≈3.2 GB of address space with ≈10 MB in use), so under `ulimit -v` 3 GB the
engine did not start. Now the arena is not reserved in advance (ffi/figram_rt.c), main runs on
the main thread of the process (ffi/figram_exe.c), and there is no libuv event loop thread with
an io_uring kernel thread (ffi/figram_init.cpp): the process is single-threaded. The runtime's
stack overflow handler is kept in the engine: a deep document under a small `ulimit -s` ends
the process with the message "Stack overflow detected", not with a silent SIGSEGV.
Exit status 0: everything is within the limits.
"""

import os
import resource
import subprocess
import sys
import tempfile
import time

ENGINE, DOC = sys.argv[1], sys.argv[2]
MAX_VMSIZE_KB = 64 * 1024       # address space reserved after start-up
MAX_RSS_KB = 16 * 1024          # memory in use after start-up and 2000 requests
MAX_THREADS = 1                 # only the main thread: the runtime starts no helper threads
bad = 0


def report(ok, text):
    global bad
    bad += 0 if ok else 1
    print(("ok   " if ok else "FAIL ") + text, flush=True)


REQS = b'{"id":1,"op":"hello"}\n{"id":2,"op":"start"}\n'
for mb in (32, 64, 256):
    def limit(mb=mb):
        resource.setrlimit(resource.RLIMIT_AS, (mb << 20, resource.RLIM_INFINITY))
    p = subprocess.run([ENGINE, DOC], input=REQS, capture_output=True, timeout=30, preexec_fn=limit)
    out = p.stdout.decode(errors="replace").splitlines()
    good = p.returncode == 0 and len(out) == 2 and '"protocol":2' in out[0] and '"snapshot"' in out[1]
    report(good, f"ulimit -v {mb} MiB: exit code {p.returncode}, replies {len(out)}"
           + ("" if good else f"; stderr: {p.stderr.decode(errors='replace')[:200]!r}"))

# Stack overflow: 250 nested objects with a stack of 24 and 32 KiB (with 8 MiB it is an ordinary
# document error). The environment is empty: its strings lie at the start of the main thread's stack
# and shift the limit. Without an environment both builds (official and from source) overflow with the
# message at 16–48 KiB, not always at 56–64 KiB, and below 16 KiB the stack is too small even before
# the runtime starts.
with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
    f.write('{"figram":1,"bot":' + '{"a":' * 250 + "1" + "}" * 250 + "}")
for kb in (24, 32):
    def small_stack(kb=kb):
        resource.setrlimit(resource.RLIMIT_STACK, (kb << 10, resource.RLIM_INFINITY))
    p = subprocess.run([ENGINE, f.name], input=REQS, capture_output=True, timeout=30, preexec_fn=small_stack, env={})
    err = p.stderr.decode(errors="replace")
    report(p.returncode == -6 and "Stack overflow detected" in err,
           f"ulimit -s {kb} KiB, nesting 250: exit code {p.returncode}, stderr {err.strip()[:80]!r}")
os.unlink(f.name)

p = subprocess.Popen([ENGINE, DOC], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
p.stdin.write(b'{"id":1,"op":"hello"}\n' + b"".join(b'{"id":%d,"op":"start"}\n' % i for i in range(2, 2002)))
p.stdin.flush()
for _ in range(2001):
    p.stdout.readline()
time.sleep(0.1)
st = {l.split(":")[0]: int(l.split()[1]) for l in open(f"/proc/{p.pid}/status") if l.startswith(("VmSize", "VmRSS", "Threads"))}
p.stdin.close()
p.wait(timeout=30)
report(st["VmSize"] <= MAX_VMSIZE_KB, f"address space reserved after start-up {st['VmSize'] >> 10} MiB (limit {MAX_VMSIZE_KB >> 10})")
report(st["VmRSS"] <= MAX_RSS_KB, f"memory in use after 2000 requests {st['VmRSS'] >> 10} MiB (limit {MAX_RSS_KB >> 10})")
report(st["Threads"] <= MAX_THREADS, f"threads {st['Threads']} (limit {MAX_THREADS})")
print(f"engine memory: address space {st['VmSize'] >> 10} MiB, in use {st['VmRSS'] >> 10} MiB, threads {st['Threads']}"
      + ("" if bad == 0 else f"; failed {bad}"))
sys.exit(1 if bad else 0)

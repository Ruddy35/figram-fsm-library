#!/usr/bin/env python3
"""C3 (cross-verify, independent): figram_client.Engine with the default timeout
(10 s) can never serve a document whose load takes longer than the timeout.

The client starts the process and measures the deadline of the FIRST request from
the moment of the call; the engine answers only after loading the document.
On timeout the process is killed; the next call lazily starts a NEW process (which
loads from scratch) inside that call's deadline -> the same timeout, forever.

Phases (one document DOC whose engine load time > 10 s, measured first):
  A  default timeout, host retries start() 4 times          -> 4 failures, 4 different pids
  B  CONTROL timeout = 3 x load time                           -> success
  C  default timeout, but host waits load+5 s before the first call (warm-up)
     -> first call OK; then the engine process dies once (SIGKILL, e.g. C4/C5/OOM)
     -> every later call fails again (never recovers)           (combination with C6)

Usage: c3_slow_load_client.py ENGINE DOC
"""
import os
import signal
import subprocess
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "ffi", "python-example"))
from figram_client import Engine, EngineError  # noqa: E402


def attempt(eng, label):
    t0 = time.monotonic()
    pid = eng._procs[0]._p.pid if eng._procs[0].alive else None
    try:
        r = eng.start("u1")
        res = f"OK at={r['snapshot']['at']}"
    except EngineError as e:
        res = f"EngineError: {str(e)[:60]}"
    pid_after = eng._procs[0]._p.pid
    print(f"  {label}: {res}  ({time.monotonic() - t0:.1f}s, engine pid {pid_after})", flush=True)
    return res.startswith("OK"), pid_after


def main():
    engine_bin, doc = sys.argv[1], sys.argv[2]
    t0 = time.monotonic()
    p = subprocess.run([engine_bin, doc], input=b'{"id":1,"op":"hello"}\n', capture_output=True, timeout=300)
    load = time.monotonic() - t0
    print(f"engine={engine_bin}\ndoc={doc}\nmeasured load+hello without client: {load:.1f}s rc={p.returncode} {p.stdout[:50]!r}")
    if load <= 10.5:
        print("document loads within the default timeout on this machine; pick a bigger one")
        sys.exit(2)

    print("A: Engine(binary, doc) with default timeout=10.0; host retries start() 4 times")
    eng = Engine(engine_bin, doc)
    resA = [attempt(eng, f"try {i + 1}") for i in range(4)]
    eng.close()

    print(f"B: CONTROL Engine(binary, doc, timeout={3 * load:.0f})")
    eng = Engine(engine_bin, doc, timeout=3 * load)
    resB = attempt(eng, "try 1")
    eng.close()

    print(f"C: default timeout, host warms up {load + 5:.0f}s before the first call, then one process crash")
    eng = Engine(engine_bin, doc)
    time.sleep(load + 5)
    resC1 = attempt(eng, "first call after warm-up")
    os.killpg(eng._procs[0]._p.pid, signal.SIGKILL)       # any single process failure
    time.sleep(0.5)
    resC = [attempt(eng, f"after crash, try {i + 1}") for i in range(3)]
    eng.close()

    pidsA = {pid for _, pid in resA}
    ok = (not any(r for r, _ in resA) and len(pidsA) == 4 and resB[0] and resC1[0]
          and not any(r for r, _ in resC))
    print("C3 REPRODUCED (A: 4/4 failed with 4 fresh processes; B control OK; C: never recovers after one crash)"
          if ok else "C3 NOT REPRODUCED / control red")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()

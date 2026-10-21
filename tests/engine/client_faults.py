"""The client never hangs, whatever the engine does.

The fake "engines" (test doubles, sh) violate the protocol in different ways;
every request from many threads must end with a controlled error within a
bounded time, and the next request must go to a new, healthy process. The
doubles fail on a request after answering hello (the client sends hello when
it starts a process and waits for the reply); failures before readiness are
checked separately: the constructor must fail with EngineError within
start_timeout.

    python3 client_faults.py ENGINE bot.json
"""

import os
import stat
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "clients", "python"))
from figram_client import Engine, EngineError

HELLO = 'read l; echo \'{"id":0,"protocol":1,"states":1}\'; '
FAKES = {
    "invalid JSON": 'read l; echo "{oops"; sleep 30',
    "unknown id": 'read l; echo \'{"id": 999, "snapshot": {}}\'; sleep 30',
    "reply without id": 'read l; echo \'{"snapshot": {}}\'; sleep 30',
    "id = true": 'read l; echo \'{"id": true, "snapshot": {"at": "a"}, "actions": []}\'; sleep 30',
    "empty result": 'read l; echo \'{"id": 1}\'; sleep 30',
    "result + error": 'read l; echo \'{"id": 1, "snapshot": {"at": "a"}, "actions": [], "error": "x"}\'; sleep 30',
    "seq = true": 'read l; echo \'{"id": 1, "snapshot": {"at": "a", "seq": true}, "actions": []}\'; sleep 30',
    "unknown action": 'read l; echo \'{"id": 1, "snapshot": {"at": "a"}, "actions": [{"jump": "x"}]}\'; sleep 30',
    "no reply": 'while read l; do :; done',
    "output closed": 'read l; exit 0',
    "crash": 'read l; kill -SEGV $$',
    "does not read input": 'sleep 30',
    "garbage, does not read input": 'sleep 0.3; echo "{oops"; sleep 30',
}
BIG = {"at": "home", "resume": {f"s{i}": "m" for i in range(7000)}}

real_engine, doc = sys.argv[1], sys.argv[2]
ok = True
with tempfile.TemporaryDirectory() as d:
    for name, body in FAKES.items():
        path = os.path.join(d, "fake.sh")
        with open(path, "w") as f:
            f.write(f"#!/bin/sh\n{HELLO}{body}\n")
        os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)
        engine = Engine(path, doc, processes=1, timeout=1.0)
        results = []

        def worker(i):
            try:
                if i % 2:
                    engine.step("u", BIG, "btn:x")   # a large request (~110 KB)
                else:
                    engine.start("u")
                results.append("reply")
            except EngineError:
                results.append("error")

        t0 = time.monotonic()
        threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(10)
        hung = sum(t.is_alive() for t in threads)
        took = time.monotonic() - t0
        t1 = time.monotonic()
        engine.close()
        close_took = time.monotonic() - t1
        good = hung == 0 and results.count("error") == 8 and took < 3.0 and close_took < 3.0
        ok &= good
        print(f"{'ok  ' if good else 'FAIL'} {name}: errors {results.count('error')} of 8, "
              f"hung {hung}, {took:.1f} s, close {close_took:.1f} s")

    # Many concurrent requests to one process that does not read its input:
    # together ~300 KB, more than the pipe buffer (64 KB), so the writer blocks.
    # Every call must fail within one timeout.
    path = os.path.join(d, "fake.sh")
    with open(path, "w") as f:
        f.write(f"#!/bin/sh\n{HELLO}sleep 30\n")
    os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)
    engine = Engine(path, doc, processes=1, timeout=1.0)
    snap = {"at": "home", "resume": {f"s{i}": "m" for i in range(60)}}
    errors = []
    durations = []

    def small():
        t = time.monotonic()
        try:
            engine.step("u", snap, "btn:x")
        except EngineError:
            errors.append(1)
        durations.append(time.monotonic() - t)

    t0 = time.monotonic()
    threads = [threading.Thread(target=small) for _ in range(300)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    engine.close()
    took = time.monotonic() - t0
    hung = sum(t.is_alive() for t in threads)
    good = len(errors) == 300 and hung == 0 and max(durations) < 2.0
    ok &= good
    print(f"{'ok  ' if good else 'FAIL'} 300 concurrent requests (~300 KB) with blocked input: "
          f"errors {len(errors)}, hung {hung}, slowest call {max(durations):.2f} s, total {took:.1f} s")

    # Failures before readiness (start-up phase): the constructor waits for hello and
    # raises EngineError within a bounded time instead of hanging or returning the pool.
    START = {
        "garbage instead of hello": 'read l; echo "{oops"; sleep 30',
        "exit before hello": 'exit 1',
        "crash before hello": 'kill -SEGV $$',
        "does not answer hello": 'sleep 30',
        "hello of the wrong form": 'read l; echo \'{"id": 0, "protocol": 1}\'; sleep 30',
    }
    for name, body in START.items():
        path = os.path.join(d, "fake.sh")
        with open(path, "w") as f:
            f.write(f"#!/bin/sh\n{body}\n")
        os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)
        t0 = time.monotonic()
        try:
            Engine(path, doc, processes=2, timeout=1.0, start_timeout=1.0).close()
            r = "pool created"
        except EngineError:
            r = "error"
        took = time.monotonic() - t0
        good = r == "error" and took < 3.0
        ok &= good
        print(f"{'ok  ' if good else 'FAIL'} start, {name}: {r}, {took:.1f} s (start_timeout 1 s)")

    # After a failure the pool replaces the process: the next request to the real engine succeeds.
    engine = Engine(real_engine, doc, processes=1, timeout=2.0)
    engine._procs[0]._fail("injected failure")
    try:
        engine.start("u")          # no longer goes to the failed process
    except EngineError:
        pass
    try:
        r = engine.start("u")
        print("ok   replacement of the failed process:", r["snapshot"]["at"])
    except EngineError as e:
        ok = False
        print("FAIL replacement of the failed process:", e)
    engine.close()

print("client is robust" if ok else "CLIENT HANGS OR DOES NOT RECOVER")
sys.exit(0 if ok else 1)

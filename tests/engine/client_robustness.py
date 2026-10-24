"""Robustness of the client: what client_faults.py and client_contract.py do not
catch. The fake "engines" are test doubles (sh, Python), plus the real engine.
When the client starts a process it sends hello with id 0 and waits for the
reply: the sh doubles answer it first (HELLO) and fail afterwards.

    python3 client_robustness.py ENGINE pizza.bot.json features.bot.json

R1  a failure that can be detected at once (invalid JSON, a foreign id, closed
    output, a crash, a wrong form, stdout closed) wakes all waiting calls at
    once, not at their own deadlines
R2  300 "failure → replacement" cycles: threads, descriptors, children and
    zombies do not grow; close() of a healthy pool is instant (stdin is closed)
R3  after a failure the pending table is empty; a call to a failed process
    fails at once, without waiting for the deadline (also on a failure between
    the readiness check and the registration of the call)
R4  late reply of a slow engine: the call fails at its deadline, the process is
    not killed, the late reply is discarded, the next request gets its own
    reply from the same process; an engine under SIGSTOP: the call fails at its
    deadline, the process is reaped after hang_timeout
R5  a string with a lone surrogate: EngineError, the pending table does not grow
R6  after close(): EngineError, no new processes (also with close() in the
    middle of active calls)
R7  the binary is missing when a process is replaced: EngineError
R8  a snapshot that the engine cannot parse at the text level (keys 1 and "1",
    True and "true", nesting > 256) is rejected by the client before sending:
    EngineError for this call only, the process is not restarted, the
    neighbours' requests go through
R9  SIGKILL is never sent to the process group of an already reaped pid

Regression checks of the client fixes (Python test doubles, pyfake, plus the real engine):
R10 start-up phase: loading longer than timeout does not get in the way (a
    double and the real engine with a slow document); after a failure of a warm
    process the replacement waits for loading; start_timeout; the call deadline
    counts from readiness; an unknown protocol version fails the start
R11 a timeout fails only its own call: the shared process is not killed, the
    late reply is discarded, the neighbours get their replies (a double and the
    real engine with a "poisonous" snapshot); a hung process is replaced after
    hang_timeout
R12 an error reply with id:null fails only the oldest request (the new protocol
    with code and the old one without), the process is not killed; other
    violations still kill it
R13 stderr tail and exit status in EngineError (a document error); exponential
    delay between restarts; the delay resets after the process has worked
R14 docstring: the host keeps the order of events (docs/host-contract.md, section 1)
R15 the length of a reply line is limited (by default and configurable)
R16 close() of a pool of N hung processes: one shared deadline
R17 the overall deadline of Engine.step (two requests + the conditions function)
R18 a reply is strictly UTF-8: surrogates (as bytes and escaped) are rejected
R19 EngineError.code / retryable; an error reply of the wrong form is a violation
R20 start(key, seq): the seq field goes into the request only when it is given
"""

import json
import os
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "clients", "python"))
import figram_client
from figram_client import Engine, EngineError

engine_bin, pizza, features = sys.argv[1], sys.argv[2], sys.argv[3]
ok = True


def report(good, text):
    global ok
    ok &= bool(good)
    print(f"{'ok  ' if good else 'FAIL'} {text}", flush=True)


def children():
    me, out = os.getpid(), []
    for d in os.listdir("/proc"):
        if d.isdigit():
            try:
                with open(f"/proc/{d}/stat") as f:
                    rest = f.read().rsplit(")", 1)[1].split()
            except OSError:
                continue
            if int(rest[1]) == me:
                out.append(rest[0])
    return out


def wait_children(n, limit=3.0):
    t0 = time.monotonic()
    while len(children()) > n and time.monotonic() - t0 < limit:
        time.sleep(0.02)
    return children()


def resources():
    return (threading.active_count(), len(os.listdir("/proc/self/task")),
            len(os.listdir("/proc/self/fd")))


def parallel(fn, n):
    res, times, lk = [], [], threading.Lock()

    def w(i):
        t = time.monotonic()
        try:
            fn(i)
            r = "reply"
        except EngineError:
            r = "error"
        except Exception as e:  # noqa: BLE001
            r = type(e).__name__
        with lk:
            res.append(r)
            times.append(time.monotonic() - t)

    ts = [threading.Thread(target=w, args=(i,)) for i in range(n)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(30)
    return res, times, sum(t.is_alive() for t in ts)


tmp = tempfile.mkdtemp()


def fake(body):
    path = os.path.join(tmp, f"fake{len(os.listdir(tmp))}.sh")
    with open(path, "w") as f:
        f.write("#!/bin/sh\n" + body + "\n")
    os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)
    return path


# The client first sends {"id":0,"op":"hello"} and waits for the reply (start-up phase):
# a double that must fail on a REQUEST answers the hello first.
HELLO = 'read l; echo \'{"id":0,"protocol":2,"states":1}\'; '
RESP = '{"id":1,"snapshot":{"at":"a"},"actions":[]}'
# Echo: a hello reply to hello, a result with the same id to anything else.
ECHO = (r'''while read l; do id=$(echo "$l" | sed 's/^{"id":\([0-9]*\).*/\1/'); '''
        r'''case "$l" in *'"op":"hello"'*) echo "{\"id\":$id,\"protocol\":2,\"states\":1}";; '''
        r'''*) echo "{\"id\":$id,\"snapshot\":{\"at\":\"a\"},\"actions\":[]}";; esac; done''')

# R1: failures that can be detected at once wake everybody at once (deadline 6 s, threshold 1.5 s).
INSTANT = {
    "invalid JSON": 'read l; echo "{oops"; sleep 30',
    "unknown id": 'read l; echo \'{"id": 999, "snapshot": {"at": "a"}, "actions": []}\'; sleep 30',
    "output closed": 'read l; exec 1>&-; sleep 30',
    "crash": 'read l; kill -SEGV $$',
    "seq = true": 'read l; echo \'{"id": 1, "snapshot": {"at": "a", "seq": true}, "actions": []}\'; sleep 30',
    "stdout closed at once": 'exec 1>&-; while read l; do :; done',
}
for name, body in INSTANT.items():
    eng = Engine(fake(HELLO + body), pizza, timeout=6.0)
    res, times, hung = parallel(lambda i: eng.start("u"), 8)
    eng.close()
    report(res.count("error") == 8 and hung == 0 and max(times) < 1.5,
           f"R1 {name}: errors {res.count('error')} of 8, slowest {max(times):.2f} s (deadline 6 s)")

# R2: resources after many "failure → replacement" cycles (the restart delay is off, otherwise
# there would be a few replacements, not 300; the delay itself is R13). The double dies of SIGKILL:
# with SIGSEGV the cycle time would depend on the OS core dump handler (see R9); a SIGSEGV crash
# is covered by R1.
eng = Engine(fake(HELLO + 'read l; kill -KILL $$'), pizza, timeout=5.0, restart_backoff=(0, 0))
base = resources()
done = 0
slowest = 0.0
for _ in range(300):
    t0 = time.monotonic()
    parallel(lambda i: eng.start("u"), 4)
    done += 1
    slowest = max(slowest, time.monotonic() - t0)
    if time.monotonic() - t0 > 2.5:   # the failure is detected at once; do not wait 300 × the deadline
        break
eng.close()
left = wait_children(0)
time.sleep(0.2)
end = resources()
report(done == 300 and end[0] <= base[0] + 1 and end[1] <= base[1] + 1 and end[2] <= base[2] + 2 and not left,
       f"R2 {done} of 300 failure cycles (cycle < 2.5 s, slowest {slowest:.2f} s): Python threads {base[0]}→{end[0]}, OS threads {base[1]}→{end[1]}, "
       f"fd {base[2]}→{end[2]}, children after close {len(left)}")
eng = Engine(engine_bin, pizza, processes=2, timeout=5.0)
eng.start("a"), eng.start("b")
t0 = time.monotonic()
eng.close()
took = time.monotonic() - t0
report(took < 1.0 and not wait_children(0, 1.0), f"R2 close() of a healthy pool of 2 processes: {took:.2f} s (deadline 5 s)")

# R3: the pending table after a failure; a call to a failed process.
eng = Engine(fake(HELLO + 'while read l; do :; done'), pizza, timeout=6.0)
p = eng._procs[0]
th = [threading.Thread(target=lambda: parallel(lambda i: p.call({"op": "start"}), 4))]
th[0].start()
time.sleep(0.3)
p._fail("injected failure")
th[0].join(10)
t0 = time.monotonic()
try:
    p.call({"op": "start"})
    r = "reply"
except EngineError:
    r = "error"
took = time.monotonic() - t0
report(len(p._pending) == 0 and r == "error" and took < 0.5,
       f"R3 after a failure: pending {len(p._pending)}, a call to the failed process — {r} in {took:.2f} s")
eng.close()
# R3 (a race, made deterministic): the process fails between the readiness check
# and the registration of the call: the call fails at once, not at its deadline.
eng = Engine(fake(HELLO + 'while read l; do :; done'), pizza, timeout=3.0)
p = eng._procs[0]
real_ready = p.wait_ready


def ready_then_fail():
    real_ready()
    p._fail("injected failure")


p.wait_ready = ready_then_fail
t0 = time.monotonic()
try:
    p.call({"op": "start"})
    r = "reply"
except EngineError:
    r = "error"
took = time.monotonic() - t0
eng.close()
report(r == "error" and took < 0.5 and len(p._pending) == 0,
       f"R3 failure between the readiness check and the registration: {r} in {took:.2f} s (deadline 3 s), pending {len(p._pending)}")

# R4: a late reply and SIGSTOP. A timeout fails only its own call: the process is
# not killed, the late reply is discarded, the next request gets its own reply
# from the same process. A hung (SIGSTOP) process is replaced after
# hang_timeout.
late = os.path.join(tmp, "late-once")   # only the first request is slow
eng = Engine(fake(HELLO + f"if [ ! -e {late} ]; then touch {late}; read l; sleep 3; echo '{RESP}'; fi; " + ECHO),
             pizza, timeout=1.0)
pid0 = eng._procs[0]._p.pid
t0 = time.monotonic()
try:
    eng.start("u")
    first = "reply"
except EngineError:
    first = "error"
took = time.monotonic() - t0
time.sleep(max(0.0, t0 + 3.5 - time.monotonic()))   # the late reply has arrived by now
try:
    second = eng.start("u")["snapshot"]["at"]
except EngineError as e:
    second = f"error {e}"
same = eng._procs[0].alive and eng._procs[0]._p.pid == pid0
eng.close()
report(first == "error" and 0.9 < took < 2.0 and second == "a" and same,
       f"R4 late reply: first call — {first} in {took:.2f} s, next — {second}, same process {same}")
eng = Engine(engine_bin, pizza, timeout=1.0, hang_timeout=1.5)
pid = eng._procs[0]._p.pid
os.kill(pid, signal.SIGSTOP)
res, times, hung = parallel(lambda i: eng.start("u"), 4)
gone = not os.path.exists(f"/proc/{pid}") or not wait_children(0, 3.0)
eng.close()
report(res.count("error") == 4 and max(times) < 2.0 and gone,
       f"R4 SIGSTOP: errors {res.count('error')} of 4, slowest {max(times):.2f} s, "
       f"process reaped after hang_timeout 1.5 s {gone}")

# R5: a lone surrogate.
eng = Engine(engine_bin, pizza, timeout=5.0)
p = eng._procs[0]
kinds = {}
for i in range(200):
    try:
        eng.step("u", {"at": "menu"} if i % 2 else {"at": "menu\udc80"}, "btn:\ud83d")
        k = "reply"
    except EngineError:
        k = "EngineError"
    except Exception as e:  # noqa: BLE001
        k = type(e).__name__
    kinds[k] = kinds.get(k, 0) + 1
alive = p.alive and "snapshot" in eng.start("u")
report(kinds == {"EngineError": 200} and len(p._pending) == 0 and alive,
       f"R5 200 requests with a lone surrogate: {kinds}, pending {len(p._pending)}, process alive {alive}")
eng.close()

# R6: after close.
wait_children(0)
eng = Engine(engine_bin, pizza, processes=2, timeout=2.0)
eng.start("a")
eng.close()
time.sleep(0.2)
res = []
for key in "abcd":
    try:
        eng.start(key)
        res.append("reply")
    except EngineError:
        res.append("error")
left = wait_children(0, 1.0)
report(res == ["error"] * 4 and not left, f"R6 calls after close(): {res}, live processes {len(left)}")
eng = Engine(engine_bin, pizza, processes=2, timeout=2.0)
stop = time.monotonic() + 0.8


def busy(i):
    while time.monotonic() < stop:
        try:
            eng.start(f"k{i}")
        except EngineError:
            pass


ths = [threading.Thread(target=busy, args=(i,)) for i in range(4)]
for t in ths:
    t.start()
time.sleep(0.3)
eng.close()
for t in ths:
    t.join(10)
left = wait_children(0, 1.0)
report(not left and not any(t.is_alive() for t in ths),
       f"R6 close() in the middle of calls from 4 threads: live processes afterwards {len(left)}")
for p in eng._procs:   # clean up if the test is red
    p.close()

# R6 (a race, made deterministic): a call has passed the "closed?" check and waits for the
# process replacement lock while close() runs to completion. A wrapper lock holds such a
# call back until close() is over.
eng = Engine(engine_bin, pizza, processes=1, timeout=2.0)
real_swap = eng._swap
at_lock, closed = threading.Event(), threading.Event()


class HoldingLock:
    def __enter__(self):
        if threading.current_thread() is not threading.main_thread():
            at_lock.set()
            closed.wait(5)
        return real_swap.__enter__()

    def __exit__(self, *exc):
        return real_swap.__exit__(*exc)


eng._swap = HoldingLock()
eng._procs[0]._fail("injected failure")   # so that the call goes to the process replacement
out = []


def racer():
    try:
        eng.start("u")
        out.append("reply")
    except EngineError:
        out.append("error")


t = threading.Thread(target=racer)
t.start()
at_lock.wait(5)
eng.close()
closed.set()
t.join(10)
left = wait_children(0, 1.0)
report(out == ["error"] and not left,
       f"R6 a call that waited for the lock during close(): {out}, live processes {len(left)}")
for p in eng._procs:   # clean up if the test is red
    p.close()

# R7: the binary is missing when the process is replaced.
copy = os.path.join(tmp, "engine-copy")
shutil.copy(engine_bin, copy)
eng = Engine(copy, pizza, timeout=2.0)
os.remove(copy)
eng._procs[0]._fail("injected failure")
kinds = {}
for _ in range(3):
    try:
        eng.start("u")
        k = "reply"
    except EngineError:
        k = "EngineError"
    except Exception as e:  # noqa: BLE001
        k = type(e).__name__
    kinds[k] = kinds.get(k, 0) + 1
eng.close()
report(kinds == {"EngineError": 3}, f"R7 process replacement without the binary: {kinds}")

# R8: a line that the engine cannot parse (an id:null reply) does not break the neighbours.
deep = {}
cur = deep
for _ in range(300):
    cur["a"] = {}
    cur = cur["a"]
BAD = {"keys 1 and \"1\"": {"at": "menu", "resume": {1: "menu", "1": "menu"}},
       "keys True and \"true\"": {"at": "menu", "timers": {True: 1, "true": 2}},
       "nesting 300": {"at": "menu", "x": deep}}
for name, snap in BAD.items():
    eng = Engine(engine_bin, pizza, processes=1, timeout=5.0)
    pid0 = eng._procs[0]._p.pid
    halt = threading.Event()
    errs = [0, 0]

    def neighbour(i):
        s = eng.start(f"n{i}")["snapshot"]
        while not halt.is_set():
            try:
                eng.step(f"n{i}", s, "btn:order")
                errs[1] += 1
            except EngineError:
                errs[0] += 1

    ths = [threading.Thread(target=neighbour, args=(i,)) for i in range(4)]
    for t in ths:
        t.start()
    time.sleep(0.2)
    kinds = {}
    for _ in range(5):
        try:
            eng.step("evil", snap, "btn:x")
            k = "reply"
        except EngineError as e:
            # rejected by the client before sending (_check_wire), not by the engine
            k = "EngineError" if "not JSON-serializable" in str(e) else f"EngineError from the engine: {e}"
        except Exception as e:  # noqa: BLE001
            k = type(e).__name__
        kinds[k] = kinds.get(k, 0) + 1
        time.sleep(0.05)
    halt.set()
    for t in ths:
        t.join(10)
    same = eng._procs[0]._p.pid == pid0 and eng._procs[0].alive
    eng.close()
    report(kinds == {"EngineError": 5} and errs[0] == 0 and same,
           f"R8 {name}: bad call {kinds}; neighbours: errors {errs[0]}, replies {errs[1]}; same process {same}")

# R9: killpg only for a pid that has not been reaped yet.
calls = [0, 0]
lk = threading.Lock()
real_killpg = os.killpg


def spy(pid, sig):
    with lk:
        calls[0] += 1
        if not os.path.exists(f"/proc/{pid}"):
            calls[1] += 1
    return real_killpg(pid, sig)


figram_client.os.killpg = spy
slow = []
try:
    # The double dies of SIGKILL, not of SIGSEGV: on SIGSEGV the kernel first hands the core
    # dump to the core_pattern handler (the system crash handler on GitHub runners), and the
    # pipes of the process close only after it, so the iteration time would measure the OS,
    # not the client (on a busy runner up to 8 such crashes take > 1.5 s per iteration).
    eng = Engine(fake(HELLO + 'read l; kill -KILL $$'), pizza, timeout=2.0, restart_backoff=(0, 0))
    for _ in range(200):
        t0 = time.monotonic()
        parallel(lambda i: eng.start("u"), 8)
        dt = time.monotonic() - t0
        if dt > 1.5:   # the failure is detected at once; do not wait 200 × the deadline
            slow.append(dt)
            break
    eng.close()
    eng = Engine(engine_bin, pizza, processes=2, timeout=2.0)
    for _ in range(50):
        eng.start("a")
        eng.start("b")
    eng.close()
finally:
    figram_client.os.killpg = real_killpg
report(calls[1] == 0 and calls[0] > 0,
       f"R9 killpg: calls {calls[0]}, of them for an already reaped (free) pid {calls[1]}")
report(not slow, f"R9 a failure of the double is noticed at once: iterations longer than 1.5 s {len(slow)}"
       + (f" ({slow[0]:.2f} s)" if slow else ""))

# ---------------------------------------------------------------------------
# R10–R20: regression checks of the client fixes. pyfake creates TEST DOUBLES of the engine
# in Python (not the real figram-engine): they answer hello and the requests by a script,
# so they also work with a client that does not send hello; with such a client these checks
# fail on the merits, not because of the handshake.

PYHEAD = f"""#!{sys.executable} -I
# TEST DOUBLE of figram-engine (client_robustness.py), not the real engine.
import json, os, signal, sys, time
def out(o):
    sys.stdout.write(json.dumps(o, ensure_ascii=False) + "\\n"); sys.stdout.flush()
def raw(b):
    sys.stdout.buffer.write(b + b"\\n"); sys.stdout.buffer.flush()
def hello(rid, protocol=2, **extra):
    out({{"id": rid, "protocol": protocol, "states": 1, **extra}})
def result(rid, at="a"):
    out({{"id": rid, "snapshot": {{"at": at}}, "actions": []}})
def requests():
    for line in sys.stdin:
        r = json.loads(line)
        if r.get("op") == "hello":
            hello(r["id"])
            continue
        yield r
"""


def pyfake(body):
    path = os.path.join(tmp, f"pyfake{len(os.listdir(tmp))}.py")
    with open(path, "w") as f:
        f.write(PYHEAD + body + "\n")
    os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)
    return path


def counter(name):
    """Launch counter of a double: (path, a line of code for the start of the double)."""
    path = os.path.join(tmp, name)
    return path, f"open({path!r}, 'a').write(str(os.getpid()) + '\\n')\n"


def launches(path):
    try:
        with open(path) as f:
            return len(f.read().split())
    except FileNotFoundError:
        return 0


def attempt(fn):
    try:
        return fn()
    except EngineError:
        return "error"
    except Exception as e:  # noqa: BLE001
        return type(e).__name__


def kill_alive(eng, i=0):
    """SIGKILL to the process group of the slot, only if the client considers the
    process alive (the pid is not reaped yet), and wait until the client notices
    the failure (EOF of the output)."""
    p = eng._procs[i]
    if p.alive:
        try:
            os.killpg(p._p.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    t0 = time.monotonic()
    while p.alive and time.monotonic() - t0 < 5:
        time.sleep(0.01)


def hwm_mb():
    with open("/proc/self/status") as f:
        for line in f:
            if line.startswith("VmHWM:"):
                return int(line.split()[1]) / 1024
    return 0.0


def unique_doc(n):
    """A document that takes superlinearly long to load: every message has a
    button of its own."""
    names = [f"m{i}" for i in range(n)]
    ch = {m: {"on": {f"btn:b{i}": [{"to": names[(i + 1) % n]}]}} for i, m in enumerate(names)}
    return {"figram": 1, "bot": {"initial": names[0], "children": ch}}


def neighbours(eng, n, check):
    """n threads send their steps until stop is set; check(i, k) → bool (the reply
    is their own and correct). Counters {ok, err, wrong, late_ok}: late_ok counts
    correct replies after the moment stats['after'], if it is set."""
    stats = {"ok": 0, "err": 0, "wrong": 0, "late_ok": 0, "after": None}
    lk = threading.Lock()
    stop = threading.Event()

    def run(i):
        k = 0
        while not stop.is_set():
            k += 1
            try:
                key = "ok" if check(i, k) else "wrong"
            except EngineError:
                key = "err"
            with lk:
                stats[key] += 1
                if key == "ok" and stats["after"] is not None and time.monotonic() > stats["after"]:
                    stats["late_ok"] += 1
            time.sleep(0.005)

    ths = [threading.Thread(target=run, args=(i,)) for i in range(n)]
    for t in ths:
        t.start()
    return ths, stats, stop


def drained(eng, key, limit=60.0):
    """Wait until the process answers in time again (the queue of slow requests
    is done): call start until it answers."""
    t0 = time.monotonic()
    while time.monotonic() - t0 < limit:
        if attempt(lambda: eng.start(key)) != "error":
            return True
    return False


def finish(ths, st, stop, eng, settle=1.0):
    """Slow requests done → `settle` more seconds of neighbour work → stop."""
    ok = drained(eng, "drain")
    st["after"] = time.monotonic()
    time.sleep(settle)
    stop.set()
    for t in ths:
        t.join(30)
    return ok


def r10_startup():
    """R10: loading takes longer than timeout; the replacement after a failure waits for loading."""
    cnt, line = counter("r10.count")
    slow = pyfake(line + "time.sleep(2.5)\nfor r in requests():\n    result(r['id'], 'loaded')\n")
    t0 = time.monotonic()
    eng = Engine(slow, pizza, timeout=1.0)
    try:
        res = [attempt(lambda: eng.start("u")["snapshot"]["at"]) for _ in range(3)]
        report(res == ["loaded"] * 3 and launches(cnt) == 1,
               f"R10 loading 2.5 s with timeout 1 s (double): replies {res}, engine launches {launches(cnt)}, "
               f"{time.monotonic() - t0:.1f} s")
        kill_alive(eng)                     # a failure of a warm process
        t1 = time.monotonic()
        res = [attempt(lambda: eng.start("u")["snapshot"]["at"]) for _ in range(3)]
        report(res == ["loaded"] * 3 and launches(cnt) == 2,
               f"R10 after a failure of a warm process the replacement waits for loading: replies {res}, "
               f"launches {launches(cnt)}, {time.monotonic() - t1:.1f} s")
    finally:
        eng.close()


def r10_real():
    """R10 on the real engine: a document with unique buttons, timeout = load time / 4."""
    load, doc, n = 0.0, None, 0
    for n in (360, 600):
        doc = os.path.join(tmp, f"unique-{n}.bot.json")
        with open(doc, "w") as f:
            json.dump(unique_doc(n), f)
        t0 = time.monotonic()
        subprocess.run([engine_bin, doc], input=b'{"id":1,"op":"hello"}\n', capture_output=True, timeout=300)
        load = time.monotonic() - t0
        if load >= 0.8:
            break
    if load < 0.8:
        report(True, f"R10 real engine: unique-{n} loads in {load:.2f} s, faster than the threshold of the check, skipped")
        return
    timeout = max(0.1, load / 4)
    eng = Engine(engine_bin, doc, timeout=timeout)
    try:
        r1 = attempt(lambda: eng.start("u")["snapshot"]["at"])
        kill_alive(eng)
        r2 = attempt(lambda: eng.start("u")["snapshot"]["at"])
    finally:
        eng.close()
    report(r1 == "m0" and r2 == "m0",
           f"R10 real engine, unique-{n} loads in {load:.1f} s, timeout {timeout:.2f} s: "
           f"first call — {r1}, after a process failure — {r2}")


def r10_options():
    """R10: start_timeout, the call deadline from readiness, the protocol version in hello."""
    never = pyfake("time.sleep(60)\n")
    t0 = time.monotonic()
    try:
        Engine(never, pizza, timeout=0.5, start_timeout=1.0).close()
        r = "created"
    except EngineError as e:
        r = f"EngineError: {e}"
    took = time.monotonic() - t0
    left = wait_children(0, 3.0)
    report(r.startswith("EngineError") and "hello" in r and 0.9 < took < 1.8 and not left,
           f"R10 start_timeout 1 s, the engine does not answer hello: '{r[:80]}', {took:.1f} s, children {len(left)}")
    eng = Engine(never, pizza, timeout=0.5, start_timeout=1.0, wait_ready=False)   # nobody waits
    p = eng._procs[0]
    t0 = time.monotonic()
    while p.alive and time.monotonic() - t0 < 5:
        time.sleep(0.02)
    took = time.monotonic() - t0
    gone = not wait_children(0, 2.0)
    eng.close()
    report(not p.alive and took < 1.8 and gone,
           f"R10 start_timeout 1 s without waiting calls: process marked failed after {took:.1f} s and killed {gone}")

    slow = pyfake("time.sleep(2.0)\nfor r in requests():\n    result(r['id'], 'loaded')\n")
    t0 = time.monotonic()
    eng = Engine(slow, pizza, timeout=0.5, wait_ready=False)
    created = time.monotonic() - t0
    try:
        got = {}
        th = threading.Thread(target=lambda: got.update(
            step=attempt(lambda: eng.step("v", {"at": "a"}, "btn:x")["snapshot"]["at"])))
        th.start()
        r = attempt(lambda: eng.start("u")["snapshot"]["at"])
        took = time.monotonic() - t0
        th.join(10)
    finally:
        eng.close()
    report(created < 0.5 and r == "loaded" and got.get("step") == "loaded" and took > 1.5,
           f"R10 wait_ready=False: constructor {created:.2f} s, calls during loading — start {r}, "
           f"step {got.get('step')} after {took:.1f} s (timeout 0.5 s and the step deadline count from readiness)")

    for what, hello_args, expect_ok in (("protocol 1", "protocol=1", False),
                                        ("protocol 3", "protocol=3", False),
                                        ("field mutant (test engine)", "mutant='x'", True)):
        fake = pyfake("for line in sys.stdin:\n    r = json.loads(line)\n"
                      f"    if r.get('op') == 'hello':\n        hello(r['id'], {hello_args})\n"
                      "    else:\n        result(r['id'], 'ok')\n")
        try:
            eng = Engine(fake, pizza, timeout=2.0)
            r = attempt(lambda: eng.start("u")["snapshot"]["at"])
            eng.close()
        except EngineError as e:
            r = f"EngineError: {e}"
        good = r == "ok" if expect_ok else (r.startswith("EngineError") and f"engine {what}" in r)
        report(good, f"R10 hello with {what}: {r[:100]}")


def r11_slow_request():
    """R11: a slow request and the host's retry do not kill the shared process."""
    cnt, line = counter("r11.count")
    fake = pyfake(line + "for r in requests():\n"
                  "    if r.get('snapshot', {}).get('at') == 'slow':\n        time.sleep(1.5)\n"
                  "    result(r['id'], r['op'] + ':' + r.get('event', ''))\n")
    eng = Engine(fake, pizza, timeout=0.6)
    try:
        pid0 = eng._procs[0]._p.pid
        ths, st, stop = neighbours(eng, 4, lambda i, k: eng.step(
            f"n{i}", {"at": "x"}, f"btn:n{i}-{k}")["snapshot"]["at"] == f"step:btn:n{i}-{k}")
        time.sleep(0.2)
        poison = [attempt(lambda: eng.step("evil", {"at": "slow"}, "btn:p")) for _ in range(2)]
        finish(ths, st, stop, eng)
        p = eng._procs[0]
        same = p.alive and p._p.pid == pid0
        final = attempt(lambda: eng.start("u")["snapshot"]["at"])
    finally:
        eng.close()
    report(poison == ["error"] * 2 and st["wrong"] == 0 and st["late_ok"] > 0 and same
           and launches(cnt) == 1 and final == "start:",
           f"R11 slow request (1.5 s with timeout 0.6 s) twice: {poison}; neighbours: replies {st['ok']} "
           f"(after the slow ones {st['late_ok']}), errors {st['err']}, foreign replies {st['wrong']}; "
           f"same process {same}, launches {launches(cnt)}")


def r11_real():
    """R11 on the real engine: a "poisonous" snapshot (resume with K entries) takes long to parse."""
    took, K = 0.0, 0
    for K in (20000, 30000):
        snap = {"at": "menu", "resume": {f"s{i}": "menu" for i in range(K)}}
        req = json.dumps({"id": 2, "op": "step", "snapshot": snap, "event": "btn:x"}).encode()
        p = subprocess.Popen([engine_bin, pizza], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL)
        p.stdin.write(b'{"id":1,"op":"hello"}\n')
        p.stdin.flush()
        p.stdout.readline()
        t0 = time.monotonic()
        p.stdin.write(req + b"\n")
        p.stdin.flush()
        p.stdout.readline()
        took = time.monotonic() - t0
        p.stdin.close()
        p.wait()
        if took >= 0.9:
            break
    if took < 0.9:
        report(True, f"R11 real engine: a snapshot of {K} entries parses in {took:.2f} s, too fast, skipped")
        return
    timeout = took / 3
    eng = Engine(engine_bin, pizza, timeout=timeout)
    try:
        pid0 = eng._procs[0]._p.pid
        s0 = eng.start("n")["snapshot"]
        want = eng.step("n", s0, "btn:order")["snapshot"]
        ths, st, stop = neighbours(eng, 4, lambda i, k: eng.step(f"n{i}", s0, "btn:order")["snapshot"] == want)
        time.sleep(0.1)
        poison = [attempt(lambda: eng.step("evil", snap, "btn:x")) for _ in range(2)]
        finish(ths, st, stop, eng)
        p = eng._procs[0]
        same = p.alive and p._p.pid == pid0
    finally:
        eng.close()
    report(poison == ["error"] * 2 and st["wrong"] == 0 and st["late_ok"] > 0 and same,
           f"R11 real engine, a snapshot of {K} entries ({took:.1f} s) with timeout {timeout:.2f} s twice: "
           f"{poison}; neighbours: replies {st['ok']} (after {st['late_ok']}), errors {st['err']}, "
           f"foreign {st['wrong']}; same process {same}")


def r11_hang():
    """R11: a process that stays silent with unanswered requests is replaced after hang_timeout."""
    cnt, line = counter("r11h.count")
    # Answers only the start-up hello, then reads and stays silent.
    fake = pyfake(line + "hello(json.loads(sys.stdin.readline())['id'])\nfor l in sys.stdin:\n    pass\n")
    eng = Engine(fake, pizza, timeout=0.5, hang_timeout=1.5)
    try:
        p = eng._procs[0]
        pid = p._p.pid
        t0 = time.monotonic()
        r1 = attempt(lambda: eng.start("u"))
        while p.alive and time.monotonic() - t0 < 6:
            time.sleep(0.02)
        detected = time.monotonic() - t0
        t1 = time.monotonic()
        while os.path.exists(f"/proc/{pid}") and time.monotonic() - t1 < 3:
            time.sleep(0.02)
        gone = not os.path.exists(f"/proc/{pid}")
        r2 = attempt(lambda: eng.start("u"))
        n = launches(cnt)
        why = p.failure or ""
    finally:
        eng.close()
    report(r1 == "error" and "hung" in why and 1.2 < detected < 4.0 and gone and n == 2 and r2 == "error",
           f"R11 hung process (hang_timeout 1.5 s, timeout 0.5 s): call — {r1}, noticed after {detected:.1f} s "
           f"'{why[:60]}', reaped {gone}, the next call in a new process (launches {n})")
    # A skipped reply: the engine answered the liveness probe (hello after the
    # timeout) but not the request before it: replies out of order, an immediate failure.
    fake = pyfake("for r in requests():\n    pass\n")
    eng = Engine(fake, pizza, timeout=0.5, hang_timeout=30.0)
    try:
        p = eng._procs[0]
        r1 = attempt(lambda: eng.start("u"))
        t0 = time.monotonic()
        while p.alive and time.monotonic() - t0 < 3:
            time.sleep(0.02)
        why = p.failure or ""
    finally:
        eng.close()
    report(r1 == "error" and "out of order" in why and time.monotonic() - t0 < 3,
           f"R11 the engine skipped the reply to a request but answered the next one: '{why[:70]}'")


def r12_id_null():
    """R12: an error reply with id:null belongs to the oldest request."""
    for what, reply, code in (("new protocol", '{"id": None, "error": "line not parsed", "code": "parse_error"}',
                               "parse_error"),
                              ("old protocol", '{"id": None, "error": "line not parsed"}', None)):
        cnt, line = counter(f"r12-{code}.count")
        fake = pyfake(line + "for r in requests():\n"
                      f"    if r.get('event') == 'btn:bad':\n        out({reply})\n"
                      "    else:\n        result(r['id'], r['op'] + ':' + r.get('event', ''))\n")
        eng = Engine(fake, pizza, timeout=2.0)
        try:
            pid0 = eng._procs[0]._p.pid
            ths, st, stop = neighbours(eng, 4, lambda i, k: eng.step(
                f"n{i}", {"at": "x"}, f"btn:n{i}-{k}")["snapshot"]["at"] == f"step:btn:n{i}-{k}")
            time.sleep(0.1)
            bad = []
            for _ in range(5):
                try:
                    eng.step("evil", {"at": "x"}, "btn:bad")
                    bad.append("reply")
                except EngineError as e:
                    bad.append((getattr(e, "code", "?"), getattr(e, "retryable", "?")))
                time.sleep(0.05)
            time.sleep(0.5)
            stop.set()
            for t in ths:
                t.join(10)
            same = eng._procs[0].alive and eng._procs[0]._p.pid == pid0
        finally:
            eng.close()
        report(bad == [(code, False)] * 5 and st["err"] == 0 and st["wrong"] == 0 and st["ok"] > 0 and same
               and launches(cnt) == 1,
               f"R12 id:null ({what}): bad calls {bad[:2]}…; neighbours: replies {st['ok']}, errors {st['err']}, "
               f"foreign {st['wrong']}; same process {same}, launches {launches(cnt)}")


def r12_real():
    """R12 on the real engine: a line with a duplicate key (the client check
    _check_wire is disabled). An old engine replies with id:null, a new one with the id."""
    saved = figram_client._check_wire
    figram_client._check_wire = lambda x, depth=0: None
    try:
        eng = Engine(engine_bin, pizza, timeout=3.0)
        try:
            pid0 = eng._procs[0]._p.pid
            s0 = eng.start("n")["snapshot"]
            want = eng.step("n", s0, "btn:order")["snapshot"]
            ths, st, stop = neighbours(eng, 4, lambda i, k: eng.step(f"n{i}", s0, "btn:order")["snapshot"] == want)
            time.sleep(0.1)
            bad = []
            for _ in range(5):
                try:
                    eng.step("evil", {"at": "menu", "resume": {1: "menu", "1": "menu"}}, "btn:x")
                    bad.append("reply")
                except EngineError as e:
                    bad.append(getattr(e, "retryable", "?"))
                time.sleep(0.05)
            time.sleep(0.5)
            stop.set()
            for t in ths:
                t.join(10)
            same = eng._procs[0].alive and eng._procs[0]._p.pid == pid0
        finally:
            eng.close()
    finally:
        figram_client._check_wire = saved
    report(bad == [False] * 5 and st["err"] == 0 and st["wrong"] == 0 and same,
           f"R12 real engine, a duplicate key in the snapshot: bad calls {bad}; neighbours: replies {st['ok']}, "
           f"errors {st['err']}; same process {same}")


def r12_control():
    """R12, control: other replies without an integer id are still a protocol violation."""
    fake = pyfake("for r in requests():\n    out({'id': None, 'snapshot': {'at': 'a'}, 'actions': []})\n")
    eng = Engine(fake, pizza, timeout=2.0)
    try:
        r = attempt(lambda: eng.start("u"))
        dead = not eng._procs[0].alive
    finally:
        eng.close()
    report(r == "error" and dead, f"R12 control: a result with id:null — {r}, process marked failed {dead}")
    fake = pyfake("for line in sys.stdin:\n    r = json.loads(line)\n    if r.get('op') == 'hello':\n"
                  "        hello(r['id']); time.sleep(0.3); out({'id': None, 'error': 'x'})\n"
                  "    else:\n        result(r['id'])\n")
    eng = Engine(fake, pizza, timeout=2.0)
    try:
        time.sleep(1.0)
        dead = not eng._procs[0].alive
    finally:
        eng.close()
    report(dead, f"R12 control: an id:null error without pending requests — process failed {dead}")


def r13_doc_error():
    """R13: the reason of a failed start (the engine's stderr, the exit status) is in the EngineError text."""
    bad = os.path.join(tmp, "bad.bot.json")
    with open(bad, "w") as f:
        json.dump({"figram": 1, "bot": {"initial": "nowhere", "children": {"a": {}}}}, f)
    msg = ""
    try:
        eng = Engine(engine_bin, bad, timeout=2.0)
        try:
            eng.start("u")
        except EngineError as e:
            msg = str(e)
        finally:
            eng.close()
    except EngineError as e:
        msg = str(e)
    report("document error" in msg and "exited with code" in msg,
           f"R13 real engine, invalid document: '{msg[:140]}'")


def r13_backoff():
    """R13: exponential delay between restarts; it resets after the process has worked."""
    cnt, line = counter("r13.count")
    broken = os.path.join(tmp, "r13.broken")
    fake = pyfake(line + f"if os.path.exists({broken!r}):\n"
                  "    sys.stderr.write('document error: broken for the test\\n'); sys.stderr.flush(); sys.exit(1)\n"
                  "for r in requests():\n    result(r['id'], 'ok')\n")
    eng = Engine(fake, pizza, timeout=1.0)
    try:
        first = attempt(lambda: eng.start("u")["snapshot"]["at"])
        open(broken, "w").close()
        n0 = launches(cnt)
        kill_alive(eng)
        msgs, lk = [], threading.Lock()
        stop = time.monotonic() + 3.0

        def retry():
            while time.monotonic() < stop:
                try:
                    eng.start("u")
                except EngineError as e:
                    with lk:
                        msgs.append(str(e))
                time.sleep(0.005)

        ths = [threading.Thread(target=retry) for _ in range(4)]
        for t in ths:
            t.start()
        for t in ths:
            t.join(15)
        n = launches(cnt) - n0
        stderr_seen = sum("broken for the test" in m for m in msgs)
        report(first == "ok" and 2 <= n <= 10 and stderr_seen > 0,
               f"R13 document broken after a failure, 4 threads retry for 3 s: engine launches {n} (the delay grows), "
               f"errors {len(msgs)}, with the stderr tail {stderr_seen}")
        os.remove(broken)
        t0 = time.monotonic()
        r = "error"
        while time.monotonic() - t0 < 40:
            r = attempt(lambda: eng.start("u")["snapshot"]["at"])
            if r == "ok":
                break
            time.sleep(0.1)
        back = time.monotonic() - t0
        kill_alive(eng)                   # the process has worked: the delay resets
        again = attempt(lambda: eng.start("u")["snapshot"]["at"])
    finally:
        eng.close()
    report(r == "ok" and again == "ok",
           f"R13 document fixed: answers again after {back:.1f} s; after a failure of a process that has worked "
           f"the replacement is immediate — {again}")


def r14_doc():
    """R14: the docstring does not promise the order of events and refers to the host's lock."""
    doc = figram_client.__doc__ or ""
    good = ("in the order they are sent" not in doc and "docs/host-contract.md, section 1, step 1" in doc
            and "lock" in doc)
    report(good, "R14 docstring: the host keeps the order of a user's events with a lock "
                  "(docs/host-contract.md, section 1, step 1)")


def r15_line():
    """R15: the length of a reply line is limited."""
    endless = pyfake("for r in requests():\n    chunk = b'x' * 65536\n    while True:\n"
                     "        sys.stdout.buffer.write(chunk)\n")
    h0 = hwm_mb()
    eng = Engine(endless, pizza, timeout=3.0)
    t0 = time.monotonic()
    try:
        r = attempt(lambda: eng.start("u"))
        took = time.monotonic() - t0
    finally:
        eng.close()
    grow = hwm_mb() - h0
    report(r == "error" and took < 2.5 and grow < 100,
           f"R15 endless reply line: {r} in {took:.2f} s (deadline 3 s), peak memory growth {grow:.0f} MiB")
    fake = pyfake("for r in requests():\n    result(r['id'], 'a' * (3000 if r.get('event') == 'btn:ok' else 5000))\n")
    eng = Engine(fake, pizza, timeout=2.0, max_line=4096)
    try:
        r1 = attempt(lambda: len(eng.step("u", {"at": "a"}, "btn:ok")["snapshot"]["at"]))
        r2 = attempt(lambda: eng.step("u", {"at": "a"}, "btn:big"))
        dead = not eng._procs[0].alive
    finally:
        eng.close()
    report(r1 == 3000 and r2 == "error" and dead,
           f"R15 max_line=4096: a 3000-byte reply — {r1}, a 5000-byte reply — {r2}, process failed {dead}")


def r16_close():
    """R16: close() closes the processes at the same time, with one shared deadline."""
    fake = pyfake("signal.signal(signal.SIGTERM, signal.SIG_IGN)\nline = sys.stdin.readline()\n"
                  "if line:\n    hello(json.loads(line)['id'])\nwhile True:\n    time.sleep(1)\n")
    eng = Engine(fake, pizza, processes=4, timeout=1.0)
    t0 = time.monotonic()
    eng.close()
    took = time.monotonic() - t0
    left = wait_children(0, 2.0)
    report(took < 1.9 and not left,
           f"R16 close() of a pool of 4 processes that do not exit on EOF: {took:.2f} s (timeout 1 s), "
           f"children afterwards {len(left)}")
    # The process is still loading and would read its input only after loading:
    # close() kills it at once instead of waiting for the deadline.
    slow = pyfake("time.sleep(30)\nfor r in requests():\n    result(r['id'])\n")
    eng = Engine(slow, pizza, processes=2, timeout=2.0, wait_ready=False)
    time.sleep(0.2)
    t0 = time.monotonic()
    eng.close()
    took = time.monotonic() - t0
    left = wait_children(0, 2.0)
    report(took < 0.5 and not left,
           f"R16 close() of a pool whose processes are still loading: {took:.2f} s (timeout 2 s), children {len(left)}")


def r17_step():
    """R17: the overall deadline of Engine.step."""
    eng = Engine(engine_bin, features, timeout=0.5)
    try:
        snap = eng.start("x")["snapshot"]
        p = eng._procs[0]
        sent = [0]
        real = p.call

        def counting(*a, **k):
            sent[0] += 1
            return real(*a, **k)

        p.call = counting

        def slow_guards(names):
            time.sleep(1.3)
            return {n: True for n in names}

        t0 = time.monotonic()
        r = attempt(lambda: eng.step("x", snap, "btn:go", guards=slow_guards)["snapshot"]["at"])
        took = time.monotonic() - t0
    finally:
        eng.close()
    report(r == "error" and sent[0] == 1 and took < 2.0,
           f"R17 conditions function 1.3 s, timeout 0.5 s (default step_timeout 1 s): {r} in {took:.1f} s, "
           f"engine requests {sent[0]} (the second is not sent)")
    eng = Engine(engine_bin, features, timeout=0.5, step_timeout=5.0)
    try:
        snap = eng.start("x")["snapshot"]
        r = attempt(lambda: eng.step("x", snap, "btn:go", guards=slow_guards)["snapshot"]["at"])
    finally:
        eng.close()
    report(r == "vip", f"R17 step_timeout=5 s, conditions function 1.3 s with timeout 0.5 s: {r}")
    fake = pyfake("for r in requests():\n    if 'guards' in r:\n        time.sleep(1.5); result(r['id'], 'g')\n"
                  "    else:\n        out({'id': r['id'], 'need': ['c']})\n")
    eng = Engine(fake, pizza, timeout=2.0, step_timeout=1.0)
    try:
        def guards(names):
            time.sleep(0.5)
            return {n: True for n in names}

        t0 = time.monotonic()
        r = attempt(lambda: eng.step("u", {"at": "a"}, "btn:x", guards=guards))
        took = time.monotonic() - t0
    finally:
        eng.close()
    report(r == "error" and took < 1.5,
           f"R17 conditions 0.5 s + second request 1.5 s with step_timeout 1 s (timeout 2 s): {r} in {took:.2f} s")


def r18_utf8():
    """R18: a reply is decoded strictly as UTF-8."""
    fake = pyfake(r"""for r in requests():
    ev = r.get("event")
    if ev == "btn:cesu":
        raw(b'{"id":%d,"snapshot":{"at":"\xed\xa0\x80"},"actions":[]}' % r["id"])
    elif ev == "btn:esc":
        raw(b'{"id":%d,"snapshot":{"at":"\\ud800"},"actions":[]}' % r["id"])
    elif ev == "btn:pair":
        raw(b'{"id":%d,"snapshot":{"at":"\\ud83d\\ude00"},"actions":[]}' % r["id"])
    else:
        result(r["id"], "пицца")
""")
    res = {}
    for ev in ("btn:cesu", "btn:esc", "btn:pair", "btn:ru"):
        eng = Engine(fake, pizza, timeout=2.0)
        try:
            res[ev] = attempt(lambda: eng.step("u", {"at": "a"}, ev)["snapshot"]["at"])
        finally:
            eng.close()
    report(res == {"btn:cesu": "error", "btn:esc": "error", "btn:pair": "\U0001F600", "btn:ru": "пицца"},
           f"R18 a surrogate as bytes and escaped — error, a pair and Cyrillic text accepted: {res!r}")


def r19_codes():
    """R19: EngineError.code and retryable; an error reply of the wrong form is a violation."""
    fake = pyfake("""for r in requests():
    ev = r.get("event")
    if ev == "btn:new":
        out({"id": r["id"], "error": "snapshot rejected", "code": "bad_snapshot"})
    elif ev == "btn:old":
        out({"id": r["id"], "error": "snapshot rejected"})
    elif ev == "btn:badcode":
        out({"id": r["id"], "error": "x", "code": 5})
    elif ev == "btn:extra":
        out({"id": r["id"], "error": "x", "extra": 1})
    elif ev == "btn:slow":
        time.sleep(1.0); result(r["id"])
    else:
        result(r["id"])
""")
    eng = Engine(fake, pizza, timeout=0.5)

    def info(ev):
        try:
            eng.step("u", {"at": "a"}, ev)
            return "reply"
        except EngineError as e:
            return (getattr(e, "code", "no field"), getattr(e, "retryable", "no field"))

    try:
        pid0 = eng._procs[0]._p.pid
        new, old, slow = info("btn:new"), info("btn:old"), info("btn:slow")
        same = eng._procs[0].alive and eng._procs[0]._p.pid == pid0
        time.sleep(0.6)
        badcode = info("btn:badcode")
        dead1 = not eng._procs[0].alive
        extra = info("btn:extra")
        dead2 = not eng._procs[0].alive
    finally:
        eng.close()
    report(new == ("bad_snapshot", False) and old == (None, False) and slow == (None, True) and same,
           f"R19 engine error with code {new}, without code {old}, timeout {slow} (code, retryable); same process {same}")
    report(badcode == (None, True) and dead1 and extra == (None, True) and dead2,
           f"R19 error reply of the wrong form (code not a string, extra field) — process failure: {badcode} {dead1}, "
           f"{extra} {dead2}")
    eng = Engine(engine_bin, pizza, timeout=2.0)
    try:
        try:
            eng.step("u", {"at": "menu", "bogus": 1}, "btn:x")
            r = "reply"
        except EngineError as e:
            r = (getattr(e, "code", "no field"), getattr(e, "retryable", "no field"))
        alive = eng._procs[0].alive
    finally:
        eng.close()
    report(r != "reply" and r[1] is False and (r[0] is None or type(r[0]) is str) and alive,
           f"R19 real engine, a snapshot with an extra field: (code, retryable) = {r}, process alive {alive}")


def r20_seq():
    """R20: start(key, seq) sends the seq field only when it is given."""
    fake = pyfake("for r in requests():\n"
                  "    out({'id': r['id'], 'snapshot': {'at': 'seq' + str(r['seq']) if 'seq' in r else 'noseq'},"
                  " 'actions': []})\n")
    eng = Engine(fake, pizza, timeout=2.0)
    try:
        a = attempt(lambda: eng.start("u")["snapshot"]["at"])
        b = attempt(lambda: eng.start("u", seq=41)["snapshot"]["at"])
    finally:
        eng.close()
    report(a == "noseq" and b == "seq41", f"R20 start without seq — {a}, start(seq=41) — {b}")


for fn in (r14_doc, r15_line, r10_startup, r10_real, r10_options, r11_slow_request, r11_real, r11_hang,
           r12_id_null, r12_real, r12_control, r13_doc_error, r13_backoff, r16_close, r17_step, r18_utf8,
           r19_codes, r20_seq):
    try:
        fn()
    except Exception as e:  # noqa: BLE001 — an exception in the test fails this check
        report(False, f"{fn.__name__} ({fn.__doc__.split(':')[0] if fn.__doc__ else ''}): exception {e!r}"[:300])
    wait_children(0, 2.0)

shutil.rmtree(tmp, ignore_errors=True)
print("client is robust" if ok else "CLIENT ROBUSTNESS VIOLATED")
sys.exit(0 if ok else 1)

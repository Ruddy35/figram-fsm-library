"""The contract of the public methods of the client.

    python3 client_contract.py ENGINE features.bot.json
"""

import math
import os
import stat
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "clients", "python"))
from figram_client import Engine, EngineError

engine_bin, doc = sys.argv[1], sys.argv[2]
ok = True


def report(good, text):
    global ok
    ok &= good
    print(f"{'ok  ' if good else 'FAIL'} {text}")


def raises(fn):
    try:
        fn()
        return False
    except EngineError:
        return True


# step never returns an unfinished need.
eng = Engine(engine_bin, doc, timeout=2)
snap = eng.start("x")["snapshot"]
report(raises(lambda: eng.step("x", snap, "btn:go", guards=lambda ns: {})), "condition not filled in → EngineError")
report(raises(lambda: eng.step("x", snap, "btn:go", guards=lambda ns: {"is_vip": 1})), "value not bool → EngineError")
report(raises(lambda: eng.step("x", snap, "btn:go", guards=lambda ns: ["is_vip"])), "not a dict → EngineError")
report(raises(lambda: eng.step("x", snap, "btn:go")), "conditions needed, no function → EngineError")
r = eng.step("x", snap, "btn:go", guards=lambda ns: {n: True for n in ns})
report("snapshot" in r and r["snapshot"]["at"] == "vip", "all conditions → step result")

# start with a first event asks for its conditions the same way.
asked = []
r = eng.start("x", event="btn:go", guards=lambda ns: asked.extend(ns) or {n: True for n in ns})
report(r["snapshot"] == {"at": "vip", "seq": 1} and r["actions"] == [{"send": "vip"}] and asked == ["is_vip"],
       "start with an event: condition asked, straight to the target, the initial message not sent")
r = eng.start("x", seq=41, event="cmd:start")
report(r["snapshot"]["at"] == "home" and r["snapshot"]["seq"] == 42, "start with an event into the initial message")
report(raises(lambda: eng.start("x", event="btn:go")), "start: conditions needed, no function → EngineError")
report(raises(lambda: eng.start("x", event="timer:idle")), "start with a timer event → EngineError")
try:
    eng.start("x", guards=lambda ns: {})
    report(False, "start: guards without an event → ValueError")
except ValueError:
    report(True, "start: guards without an event → ValueError")

# Invalid arguments leave the pending table and the process untouched.
p = eng._procs[0]
bad_args = 0
for i in range(1000):
    arg = object() if i % 2 else math.nan
    try:
        eng.step("x", {"at": "home", "x": arg}, "btn:go")
    except EngineError:
        bad_args += 1
report(bad_args == 1000 and len(p._pending) == 0 and p.alive,
       f"1000 non-serializable requests: errors {bad_args}, pending {len(p._pending)}, process alive {p.alive}")
report("snapshot" in eng.start("x"), "afterwards the process serves requests")
eng.close()


# A repeated need → error; a crashed process is reaped without further requests.
def state(pid):
    try:
        with open(f"/proc/{pid}/stat") as f:
            return f.read().rsplit(")", 1)[1].split()[0]
    except (FileNotFoundError, ProcessLookupError):
        # ProcessLookupError (ESRCH): the process was reaped between open() and read().
        return "reaped"


with tempfile.TemporaryDirectory() as d:
    def fake(body):
        """A test double (sh): answers the start-up hello, then runs body."""
        path = os.path.join(d, "fake.sh")
        with open(path, "w") as f:
            f.write("#!/bin/sh\nread l; echo '{\"id\":0,\"protocol\":2,\"states\":1}'\n" + body + "\n")
        os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)
        return path

    eng = Engine(fake('read l; echo \'{"id":1,"need":["a"]}\'; read l; echo \'{"id":2,"need":["a"]}\'; sleep 30'),
                 doc, timeout=2)
    report(raises(lambda: eng.step("x", {"at": "home"}, "btn:go", guards=lambda ns: {n: True for n in ns})),
           "repeated need → EngineError")
    eng.close()

    eng = Engine(fake('read l; echo "{oops"; sleep 30'), doc, timeout=1)
    pid = eng._procs[0]._p.pid
    raises(lambda: eng.start("x"))
    t0 = time.monotonic()
    while state(pid) != "reaped" and time.monotonic() - t0 < 2:
        time.sleep(0.02)
    report(state(pid) == "reaped", f"crashed process reaped in {time.monotonic() - t0:.2f} s without new requests")
    t0 = time.monotonic()
    eng.close()
    report(time.monotonic() - t0 < 2, "close after a crash does not hang")

print("client contract holds" if ok else "CLIENT CONTRACT VIOLATED")
sys.exit(0 if ok else 1)

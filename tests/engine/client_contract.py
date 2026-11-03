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
report(r["snapshot"] == {"at": "vip", "timers": {"nudge": 1}, "seq": 1}
       and r["actions"] == [{"start_timer": "nudge", "ms": 30000, "token": 1}, {"send": "vip"}] and asked == ["is_vip"],
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

# Outcomes of actions (protocol 3): Engine.outcome sends `call:<action>:<outcome>` with the token.
def reply(fn):
    """The result of a call, or the EngineError it raised (reported as a failed check)."""
    try:
        return fn()
    except EngineError as e:
        return {"error": str(e), "snapshot": None, "actions": None}


ITEM = {"at": "item", "timers": {"remind": 3}, "seq": 5}
r = reply(lambda: eng.step("x", ITEM, "btn:check"))
report(r["actions"] == [{"call": "check_stock", "token": 6}]
       and (r["snapshot"] or {}).get("pending") == [{"call": "check_stock", "token": 6}],
       "an action with outcomes: call with the step's token, a wait in the snapshot")
waiting = {**ITEM, "pending": [{"call": "check_stock", "token": 6}], "seq": 6}
r = reply(lambda: eng.outcome("x", waiting, "check_stock", "yes", 6))
report(r["snapshot"] == {"at": "item2", "resume": {"shop": "item2"}, "seq": 7}
       and r["actions"] == [{"cancel_timer": "remind"}, {"send": "item2"}],
       f"outcome with the token of the wait: accepted, the wait removed, the arrow taken {r.get('error', '')}")
r = reply(lambda: eng.outcome("x", waiting, "check_stock", "yes", 5))
report(r["snapshot"] == waiting and r["actions"] == [], f"outcome with a stale token: ignored {r.get('error', '')}")
r = reply(lambda: eng.outcome("x", waiting, "check_stock", "failed", 6))
report(r["snapshot"] == {"at": "item", "timers": {"remind": 3}, "seq": 7}
       and r["actions"] == [{"call": "log", "detached": True}],
       f"outcome failed: the arrow without a target, a background action {r.get('error', '')}")
r = reply(lambda: eng.step("x", {"at": "home", "seq": 1}, "btn:ping"))
report(r["actions"] == [{"call": "ping", "token": 2, "detached": True}],
       "a background action with outcomes: call with token and detached")
pinged = {"at": "home", "pending": [{"call": "ping", "token": 3}, {"call": "ping", "token": 2}], "seq": 3}
r1 = reply(lambda: eng.outcome("x", pinged, "ping", "pong", 2))
r2 = reply(lambda: eng.outcome("x", r1["snapshot"], "ping", "pong", 3)) if r1["snapshot"] else r1
report((r1["snapshot"] or {}).get("pending") == [{"call": "ping", "token": 3}]
       and r2["snapshot"] == {"at": "home", "seq": 5},
       f"every: each call has its own wait, each outcome is accepted once {r2.get('error', '')}")
pizza = os.path.join(os.path.dirname(os.path.abspath(doc)), "pizza.bot.json")
peng = Engine(engine_bin, pizza, timeout=2)
placing = {"at": "placing", "resume": {"order": "placing"}, "pending": [{"call": "create_order", "token": 6}], "seq": 6}
asked = []
r = reply(lambda: peng.outcome("u", placing, "create_order", "rejected", 6,
                               guards=lambda ns: asked.extend(ns) or {n: True for n in ns}))
report(asked == ["address_rejected"] and (r["snapshot"] or {}).get("at") == "address"
       and "pending" not in (r["snapshot"] or {"pending": 1}),
       f"outcome with conditions: the guards function is asked, the conditional arrow taken {r.get('error', '')}")
try:
    peng.outcome("u", placing, "create_order", "rejected", 6)
    report(False, "outcome: conditions needed, no function → EngineError")
except EngineError as e:
    report("address_rejected" in str(e), f"outcome: conditions needed, no function → EngineError ({e})")
peng.close()


def value_error(fn):
    try:
        fn()
        return False
    except ValueError:
        return True
    except EngineError:
        return False


p = eng._procs[0]
bad = [("action", ""), ("action", None), ("outcome", ""), ("outcome", "a:b"), ("outcome", 5),
       ("token", True), ("token", -1), ("token", 2 ** 53), ("token", 6.0), ("token", "6"), ("token", None)]
refused = 0
for field, value in bad:
    args = {"action": "check_stock", "outcome": "yes", "token": 6, field: value}
    refused += value_error(lambda: eng.outcome("x", waiting, args["action"], args["outcome"], args["token"]))
report(refused == len(bad) and len(p._pending) == 0 and p.alive,
       f"outcome: malformed action, outcome or token → ValueError before sending ({refused} of {len(bad)})")
try:
    eng.step("x", waiting, "call:check_stock:yes")
    report(False, "step with an outcome event without a token → EngineError bad_request")
except EngineError as e:
    report(e.code == "bad_request" and not e.retryable, "step with an outcome event without a token → EngineError bad_request")
try:
    eng.start("x", event="call:check_stock:yes")
    report(False, "start with an outcome event → EngineError bad_request")
except EngineError as e:
    report(e.code == "bad_request" and not e.retryable, "start with an outcome event → EngineError bad_request")

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
            f.write("#!/bin/sh\nread l; echo '{\"id\":0,\"protocol\":3,\"states\":1}'\n" + body + "\n")
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

"""The time of one request grows linearly with the size of the snapshot.

A host may hand the engine a snapshot with very many timers or remembered places (a corrupted
or forged row): leaving a message, recovery (with and without a variant for the event at the
initial message) and `restart` cancel every timer of the snapshot, and entering a message
remembers its place among all the remembered places. One request is measured at two sizes, N
and 4·N entries. Linear time gives a ratio of about 4; the check requires at most 8 (quadratic
time gives about 16) and at most 3 s for the larger request.

The time is the CPU time (user + system) of an engine process that answers only that request,
less the start-up (a process answering an empty input). Unlike the wall-clock time it does not
grow when other processes take the cores: with the wall-clock time of one request, linear code
exceeded the ratio 8 in 9 of 30 runs on a 4-core machine with 4 busy processes. A measurement is
the least of up to three runs (the larger request is repeated only while it fails the check and
takes at most 3 s, so a quadratic engine is not run three times).

    python3 snapshot_scaling.py ENGINE examples/features.bot.json
"""

import json
import os
import subprocess
import sys

SMALL, LARGE = 20000, 80000
MAX_RATIO, MAX_SECONDS = 8.0, 3.0
REPEAT = 3

engine, doc = sys.argv[1:3]


def timers(n):
    return {f"t{i}": 1 for i in range(n)}


CASES = [
    # what, request for n entries
    ("leave a message: cancel all timers",
     lambda n: {"op": "step", "snapshot": {"at": "home", "timers": timers(n), "seq": 1},
                "event": "btn:go", "guards": {"is_vip": True}}),
    ("recovery: cancel all timers, enter with the event",
     lambda n: {"op": "step", "snapshot": {"at": "gone", "timers": timers(n), "seq": 1},
                "event": "cmd:help"}),
    ("recovery: cancel all timers, no variant at the initial message",
     lambda n: {"op": "step", "snapshot": {"at": "gone", "timers": timers(n), "seq": 1},
                "event": "btn:none"}),
    ("recovery: cancel all timers, a late timer event of the removed message",
     lambda n: {"op": "step", "snapshot": {"at": "gone", "timers": timers(n), "seq": 1},
                "event": "timer:t0", "token": 1}),
    ("restart: cancel all timers",
     lambda n: {"op": "restart", "snapshot": {"at": "item", "timers": timers(n), "seq": 1}}),
    ("enter a message: remember the place among all places",
     lambda n: {"op": "step", "snapshot": {"at": "home", "seq": 1,
                                           "resume": {f"s{i}": "m" for i in range(n)}},
                "event": "btn:go", "guards": {"is_vip": False}}),
]

def encode(req):
    return (json.dumps(req, separators=(",", ":")) + "\n").encode()


def cpu(line):
    """CPU time (user + system) of one engine process that answers `line`, and the reply.
    Unlike the wall-clock time it does not grow when other processes take the cores."""
    proc = subprocess.Popen([engine, "--max-request", "0", doc], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE)
    proc.stdin.write(line)
    proc.stdin.close()
    out = proc.stdout.read()
    proc.stdout.close()
    _, status, ru = os.wait4(proc.pid, 0)
    proc.returncode = os.waitstatus_to_exitcode(status)
    replies = [json.loads(x) for x in out.splitlines() if x.strip()]
    return ru.ru_utime + ru.ru_stime, (replies[0] if replies else {})


def measure(line, enough):
    """The least CPU time of up to REPEAT runs less the start-up, stopping once `enough(time)`
    holds."""
    best = None
    for _ in range(REPEAT):
        dt, reply = cpu(line)
        if "snapshot" not in reply:
            return None, reply
        best = dt if best is None else min(best, dt)
        if enough(max(best - STARTUP, 0.0)):
            break
    return max(best - STARTUP, 0.0), reply


STARTUP = min(cpu(b"")[0] for _ in range(REPEAT))
bad = ran = 0
for what, make in CASES:
    ran += 1
    small, reply = measure(encode(make(SMALL)), lambda t: False)
    large = None
    if small is not None:
        limit = MAX_RATIO * max(small, 1e-4)
        large, reply = measure(encode(make(LARGE)), lambda t: t <= limit or t > MAX_SECONDS)
    if large is None:
        print(f"FAIL {what}: no result: {str(reply)[:200]}")
        bad += 1
        continue
    ratio = large / max(small, 1e-4)
    ok = ratio <= MAX_RATIO and large <= MAX_SECONDS
    bad += not ok
    print(f"{'ok  ' if ok else 'FAIL'} {what}: {SMALL} entries {small:.3f} s, "
          f"{LARGE} entries {large:.3f} s, ratio {ratio:.1f} "
          f"(at most {MAX_RATIO}, at most {MAX_SECONDS} s)")
print(f"snapshot scaling: cases {ran}, failed {bad}")
sys.exit(1 if bad else 0)

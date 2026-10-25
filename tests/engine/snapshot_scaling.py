"""The time of one request grows linearly with the size of the snapshot.

A host may hand the engine a snapshot with very many timers or remembered places (a corrupted
or forged row): leaving a message, recovery and `restart` cancel every timer of the snapshot,
and entering a message remembers its place among all the remembered places. One request is
measured at two sizes, N and 4·N entries, in one `figram-engine` process (the start-up does not
count). Linear time gives a ratio of about 4; the check requires at most 8 (quadratic time
gives about 16) and at most 3 s for the larger request.

    python3 snapshot_scaling.py ENGINE examples/features.bot.json
"""

import json
import subprocess
import sys
import time

SMALL, LARGE = 20000, 80000
MAX_RATIO, MAX_SECONDS = 8.0, 3.0

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
    ("restart: cancel all timers",
     lambda n: {"op": "restart", "snapshot": {"at": "item", "timers": timers(n), "seq": 1}}),
    ("enter a message: remember the place among all places",
     lambda n: {"op": "step", "snapshot": {"at": "home", "seq": 1,
                                           "resume": {f"s{i}": "m" for i in range(n)}},
                "event": "btn:go", "guards": {"is_vip": False}}),
]

proc = subprocess.Popen([engine, "--max-request", "0", doc], stdin=subprocess.PIPE,
                        stdout=subprocess.PIPE)


def ask(req):
    line = (json.dumps(req, separators=(",", ":")) + "\n").encode()
    t = time.perf_counter()
    proc.stdin.write(line)
    proc.stdin.flush()
    reply = json.loads(proc.stdout.readline())
    return time.perf_counter() - t, reply


ask({"op": "hello"})
bad = 0
for what, make in CASES:
    times = {}
    for n in (SMALL, LARGE):
        dt, reply = ask(make(n))
        if "snapshot" not in reply:
            print(f"FAIL {what}, {n} entries: no result: {str(reply)[:200]}")
            bad += 1
            break
        times[n] = dt
    else:
        ratio = times[LARGE] / max(times[SMALL], 1e-4)
        ok = ratio <= MAX_RATIO and times[LARGE] <= MAX_SECONDS
        bad += not ok
        print(f"{'ok  ' if ok else 'FAIL'} {what}: {SMALL} entries {times[SMALL]:.3f} s, "
              f"{LARGE} entries {times[LARGE]:.3f} s, ratio {ratio:.1f} "
              f"(at most {MAX_RATIO}, at most {MAX_SECONDS} s)")
proc.stdin.close()
proc.wait(timeout=60)
print(f"snapshot scaling: cases {len(CASES)}, failed {bad}")
sys.exit(1 if bad else 0)

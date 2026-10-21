"""Self-test of the test harness: every corruption of the engine must make the
corresponding test red.

The fake engines are proxies to the real engine that corrupt replies, do not
answer everything, offer no mutations or hang.

    python3 harness_selftest.py ENGINE pizza.bot.json traces.jsonl FEATURES_TRACES.jsonl
"""

import os
import re
import stat
import subprocess
import sys
import tempfile
import time

engine, doc, traces, ftraces = (os.path.abspath(a) for a in sys.argv[1:5])
here = os.path.dirname(os.path.abspath(__file__))
features = os.path.join(os.path.dirname(doc), "features.bot.json")
schema = os.path.join(here, "..", "..", "schemas", "snapshot.schema.json")

PROXY = """#!/usr/bin/env python3
import json, subprocess, sys
p = subprocess.Popen([{engine!r}] + sys.argv[1:], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)
n = 0
for line in sys.stdin:
    p.stdin.write(line); p.stdin.flush()
    r = json.loads(p.stdout.readline())
    n += 1
    out = []
    {code}
    for o in out:
        print(o, flush=True)
"""

# Each corruption: code that turns the reply r into the list of output lines `out`.
EMIT = 'out.append("{oops" if r is None else json.dumps(r))'
CORRUPT = {
    "snapshot": f'if n % 7 == 3 and "snapshot" in r: r["snapshot"] = {{"at": "nowhere"}}\n    {EMIT}',
    "id": f'if n % 7 == 3: r["id"] = -999\n    {EMIT}',
    "id shifted": f'if n % 7 == 3: r["id"] += 1\n    {EMIT}',
    "not JSON": f'if n % 7 == 3: r = None\n    {EMIT}',
    "seq = true": f'if "snapshot" in r and n % 3 == 1: r["snapshot"]["seq"] = True\n    {EMIT}',
    "token = true": f'[a.update(token=True) for a in r.get("actions", []) if "start_timer" in a]\n    {EMIT}',
    "need + error": f'if "need" in r: r["error"] = "contradictory"\n    {EMIT}',
    "need + snapshot": f'if "need" in r: r["snapshot"] = {{"at": "a"}}\n    {EMIT}',
    "result + error": f'if "snapshot" in r and n % 5 == 2: r["error"] = "x"\n    {EMIT}',
    "error without code": f'if "error" in r: r.pop("code", None)\n    {EMIT}',
    "unknown code": f'if "error" in r: r["code"] = "oops"\n    {EMIT}',
    "skipped reply": f'if n != 3: {EMIT}',
    "duplicate reply": f'{EMIT}\n    if n == 3: {EMIT}',
}
TOOLS = {
    "conformance": lambda fake: [fake, f"{doc}={traces}"],
    "loadtest": lambda fake: [fake, doc, traces, "8", "1"],
    "snapshot_fixtures": lambda fake: [fake, features, schema],
}
# Corruptions that do not apply to a tool: snapshot_fixtures has no need and no
# timers; loadtest goes through the client, which catches a skipped reply by a timeout
# (checked in client_faults). The replies of conformance and loadtest contain no errors
# (and no code field): corrupting the error code is checked on snapshot_fixtures, where
# snapshots are rejected.
SKIP = {("loadtest", "skipped reply"), ("loadtest", "duplicate reply"),
        ("snapshot_fixtures", "need + error"), ("snapshot_fixtures", "need + snapshot"),
        ("snapshot_fixtures", "token = true"),
        ("conformance", "error without code"), ("conformance", "unknown code"),
        ("loadtest", "error without code"), ("loadtest", "unknown code")}

ok = True


LAST = {"out": ""}


def report(good, text):
    global ok
    ok &= good
    print(f"{'ok  ' if good else 'FAIL'} {text}", flush=True)
    if not good:   # details of the failure: the tail of the output of the tool under test
        for line in LAST["out"].strip().splitlines()[-8:]:
            print(f"      | {line}", flush=True)


def run_tool(tool, args, env=None, timeout=600):
    t0 = time.monotonic()
    r = subprocess.run([sys.executable, os.path.join(here, f"{tool}.py")] + args,
                       capture_output=True, timeout=timeout, env=env)
    LAST["out"] = (r.stdout + r.stderr).decode(errors="replace")
    return r.returncode, time.monotonic() - t0


# Only messages about a VIOLATION: lines of a successful summary ("mismatches 0", "replies 24") do not match.
DETECTED = re.compile(r"mismatches [1-9]|disagree:|MISMATCH|violates the protocol|replies \d+ of \d+|"
                      r"reply \d+:|thread error|steps done \d+ of|seq boundary: FAIL|step \d+:|: start$")


def caught():
    """The corruption is caught only if the tool reported a violation or a mismatch
    and did not crash with a traceback for an unrelated reason."""
    out = LAST["out"]
    return "Traceback" not in out and bool(DETECTED.search(out))


def alive_after(pids, limit=5.0):
    """Live (not zombie) processes from the list; waits up to `limit` seconds: in slow
    sandboxes delivering SIGKILL and the state change take time."""
    deadline = time.monotonic() + limit
    while True:
        alive = []
        for pid in pids:
            try:
                state = open(f"/proc/{pid}/stat").read().rsplit(")", 1)[1].split()[0]
                if state != "Z":
                    alive.append(pid)
            except (FileNotFoundError, ProcessLookupError):
                pass
        if not alive or time.monotonic() > deadline:
            return alive
        time.sleep(0.05)


with tempfile.TemporaryDirectory() as d:
    def write(name, text):
        path = os.path.join(d, name)
        with open(path, "w") as f:
            f.write(text)
        os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)
        return path

    for name, code in CORRUPT.items():
        fake = write("fake.py", PROXY.format(engine=engine, code=code))
        for tool, args in TOOLS.items():
            if (tool, name) in SKIP:
                continue
            rc, took = run_tool(tool, args(fake))
            report(rc != 0 and caught(),
                   f"{tool}, corruption '{name}': exit code {rc}, recognized {caught()} ({took:.1f} s)")

    # Zero mutations is a failure, not 100%.
    fake = write("nomut.sh", f'#!/bin/sh\nif [ "$1" = "--mutants" ]; then exit 0; fi\nexec {engine} "$@"\n')
    rc, _ = run_tool("mutation", [fake, f"{doc}={traces}"])
    report(rc != 0 and "below the minimum" in LAST["out"] and "Traceback" not in LAST["out"],
           f"mutation, engine without mutations: exit code {rc}")

    # A hung engine (without the deadline the check would hang for ~1000 s): the check
    # must fail BECAUSE of the deadline, much earlier, leaving no live processes.
    # The time threshold is generous: the point is "does not hang", not the speed of the machine.
    pidfile = os.path.join(d, "pid")
    fake = write("hang.sh", f"#!/bin/sh\necho $$ > {pidfile}\nsleep 1000 &\necho $! >> {pidfile}\nwait\n")
    env = dict(os.environ, FIGRAM_TEST_TIMEOUT="2")
    for tool, args in [("conformance", [fake, f"{doc}={traces}"]),
                       ("snapshot_fixtures", [fake, features, schema])]:
        rc, took = run_tool(tool, args, env=env, timeout=300)
        by_deadline = "did not finish within" in LAST["out"]
        alive = alive_after(open(pidfile).read().split())
        report(rc != 0 and by_deadline and took < 120 and not alive,
               f"{tool}, hung engine: exit code {rc}, stopped by the deadline {by_deadline}, {took:.1f} s, "
               f"live processes {len(alive)}")

    # Positive control: the real engine passes all checks.
    for tool, args in TOOLS.items():
        rc, took = run_tool(tool, args(engine))
        report(rc == 0, f"{tool}, real engine: exit code {rc} ({took:.1f} s)")
    golden = os.path.join(os.path.dirname(doc), "golden.jsonl")
    rc, took = run_tool("mutation", [engine, "--golden", golden, f"{doc}={traces}",
                                     f"{features}={ftraces}"], timeout=1800)
    report(rc == 0, f"mutation, real engine on the full corpus: exit code {rc} ({took:.1f} s)")

print("the harness catches corruption" if ok else "THE HARNESS MISSES CORRUPTION (see the FAIL lines above)")
sys.exit(0 if ok else 1)

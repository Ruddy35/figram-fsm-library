"""The engine never writes a snapshot that requests cannot carry back (docs/format.md, section 8,
`snapshot_too_large`).

    python3 snapshot_limit.py ENGINE FEATURES_DOC

A user presses `btn:ping` (an action with outcomes and `every`, features.bot.json) while no
outcome comes: every step adds a wait, and the snapshot grows. Under `--max-request 65536` the
engine must refuse the step whose new snapshot would take more than 15/16 of the limit with
`snapshot_too_large` and the request's `id`, and keep answering the requests that carry the
last snapshot it wrote: the outcome that removes a wait, and `restart`. Before this rule
(0.3.0 audit) the engine wrote a snapshot whose next request was `too_large`, and the user was
stuck: the outcome and `restart` were refused too.
Exit status 0: every check holds.
"""

import json
import subprocess
import sys

ENGINE, DOC = sys.argv[1], sys.argv[2]
LIMIT = 65536
bad = 0


def report(ok, text):
    global bad
    bad += 0 if ok else 1
    print(("ok   " if ok else "FAIL ") + text, flush=True)


def line(req):
    return (json.dumps(req, separators=(",", ":")) + "\n").encode()


p = subprocess.Popen([ENGINE, "--max-request", str(LIMIT), DOC], stdin=subprocess.PIPE, stdout=subprocess.PIPE)


def ask(req):
    p.stdin.write(line(req))
    p.stdin.flush()
    return json.loads(p.stdout.readline())


stored = {"at": "home", "seq": 5}
steps = 0
rep = {}
while steps < 5000:
    req = {"id": steps, "op": "step", "snapshot": stored, "event": "btn:ping"}
    if len(line(req)) - 1 > LIMIT:
        report(False, f"the engine wrote a snapshot ({len(stored['pending'])} waits) whose next request "
                      f"is over the limit of {LIMIT} bytes")
        break
    rep = ask(req)
    if "snapshot" not in rep:
        break
    stored = rep["snapshot"]
    steps += 1
size = len(line(stored)) - 1
waits = len(stored.get("pending", []))
report(rep.get("code") == "snapshot_too_large" and rep.get("id") == steps and set(rep) == {"id", "error", "code"},
       f"step {steps} refused: {rep.get('code')} with id {rep.get('id')}")
report(size + LIMIT // 16 <= LIMIT < size + LIMIT // 16 + 64,
       f"the last snapshot written: {waits} waits, {size} bytes, within 15/16 of the limit by less than one wait")
out = ask({"id": "o", "op": "step", "snapshot": stored, "event": "call:ping:pong",
           "token": stored["pending"][-1]["token"]})
report("snapshot" in out and len(out["snapshot"].get("pending", [])) == waits - 1,
       f"its outcome is accepted: {len(out.get('snapshot', {}).get('pending', []))} waits left")
rst = ask({"id": "r", "op": "restart", "snapshot": stored})
report("snapshot" in rst and "pending" not in rst["snapshot"], "restart with it clears the waits")
again = ask({"id": "a", "op": "step", "snapshot": stored, "event": "btn:ping"})
report(again.get("code") == "snapshot_too_large", "the growing step is refused again (deterministic)")
p.stdin.close()
p.wait(timeout=30)
# no limit: the same step is answered
p2 = subprocess.run([ENGINE, "--max-request", "0", DOC], input=line({"id": 1, "op": "step", "snapshot": stored,
                    "event": "btn:ping"}), capture_output=True, timeout=60)
report('"snapshot"' in p2.stdout.decode(), "with --max-request 0 (no limit) the step is answered")
print(f"snapshot limit: steps {steps}, waits {waits}, failed {bad}")
sys.exit(1 if bad else 0)

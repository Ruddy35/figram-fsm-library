"""The snapshot schema and the engine accept and reject the same snapshots.

    python3 snapshot_fixtures.py ENGINE bot.json schemas/snapshot.schema.json
"""

import json
import os
import subprocess
import sys

import jsonschema

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "clients", "python"))
from protocol import ChildTimeout, ProtocolError, check_reply, default_timeout, run_child

M = 9007199254740991
FIXTURES = [
    {"at": "home"},
    {"at": "home", "v": 1, "seq": 0},
    {"at": "home", "seq": M},
    {"at": "home", "seq": M + 1},
    {"at": "home", "seq": -1},
    {"at": "home", "seq": 1.5},
    {"at": "home", "seq": "1"},
    {"at": ""},
    {"at": 5},
    {},
    {"at": "home", "v": 2},
    {"at": "home", "extra": 1},
    {"at": "home", "resume": {"shop": "item"}},
    {"at": "home", "resume": {"shop": ""}},
    {"at": "home", "resume": {"": "item"}},
    {"at": "home", "resume": {"shop": 3}},
    {"at": "home", "resume": []},
    {"at": "home", "timers": {"idle": 0}},
    {"at": "home", "timers": {"idle": M}},
    {"at": "home", "timers": {"idle": M + 1}},
    {"at": "home", "timers": {"idle": -1}},
    {"at": "home", "timers": {"": 1}},
    {"at": "home", "timers": {"idle": "1"}},
    [],
]

engine, doc, schema_path = sys.argv[1:4]
validator = jsonschema.Draft202012Validator(json.load(open(schema_path)))


def ask(requests, phase):
    """Send the requests; return the replies checked against the protocol and the ids.
    Any mismatch in the number, the id or the form of a reply is a failure."""
    data = "\n".join(json.dumps({**r, "id": i}) for i, r in enumerate(requests)) + "\n"
    try:
        out = run_child([engine, doc], data.encode(), default_timeout(), phase)
    except ChildTimeout as e:
        raise SystemExit(str(e))
    lines = out.stdout.decode().split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    if out.returncode != 0 or len(lines) != len(requests):
        raise SystemExit(f"{phase}: replies {len(lines)} of {len(requests)}, exit code {out.returncode}")
    replies = []
    for i, (r, line) in enumerate(zip(requests, lines)):
        try:
            resp = json.loads(line)
            variant = check_reply(resp, i, r["op"])
        except (ValueError, ProtocolError) as e:
            raise SystemExit(f"{phase}: reply {i} violates the protocol: {e}")
        replies.append((variant, resp))
    return replies


replies = ask([{"op": "step", "snapshot": s, "event": "unknown:event"} for s in FIXTURES],
              "edge snapshots")
bad = 0
for snap, (variant, resp) in zip(FIXTURES, replies):
    by_engine = variant == "result"
    by_schema = validator.is_valid(snap)
    if by_engine != by_schema:
        bad += 1
        print(f"  disagree: {snap}: schema {by_schema}, engine {by_engine} {resp.get('error', '')}")
    elif not by_engine and resp.get("code") != "bad_snapshot":
        # a rejected snapshot has exactly the code bad_snapshot (docs/format.md, section 8, "Errors")
        bad += 1
        print(f"  disagree: {snap}: error code {resp.get('code')!r} instead of 'bad_snapshot'")
print(f"snapshots {len(FIXTURES)}, replies {len(replies)}, schema/engine mismatches {bad}")

# At the seq boundary the engine never returns a snapshot that it cannot read itself.
edge = ask([
    {"op": "step", "snapshot": {"at": "home", "seq": M}, "event": "btn:self"},
    {"op": "step", "snapshot": {"at": "home", "seq": M}, "event": "unknown:event"},
    {"op": "step", "snapshot": {"at": "home", "seq": M - 1}, "event": "btn:self"},
], "seq boundary")
edge_ok = (edge[0] == ("error", {"id": 0, "error": "seq_overflow", "code": "seq_overflow"})
           and edge[1][0] == "result" and edge[1][1]["snapshot"].get("seq") == M
           and edge[2][0] == "result" and edge[2][1]["snapshot"].get("seq") == M
           and validator.is_valid(edge[2][1]["snapshot"]))
print("seq boundary:", "ok" if edge_ok else f"FAIL {edge}")
sys.exit(1 if bad or not edge_ok else 0)

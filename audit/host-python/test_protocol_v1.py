#!/usr/bin/env python3
"""Transport of the Python host against the final engine protocol v1 (FORMAT.md §7):
error replies are exactly {id, error, code} and the host branches on `code`; `start` with `seq`
continues the persistent high-water seq (P-01, HOST.md §4).

    python3 -I test_protocol_v1.py ENGINE BOT.json      (exit 0 = all checks pass)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from engine_transport import EngineFailure, EnginePool, EngineReplyError, Faults, check_reply  # noqa: E402

engine, doc = sys.argv[1], sys.argv[2]
fails = 0


def check(ok, what):
    global fails
    print(f"{'ok  ' if ok else 'FAIL'} {what}")
    fails += 0 if ok else 1


def kind(r, rid=5, op="step"):
    try:
        return check_reply(r, rid, op)
    except EngineFailure as e:
        return f"failure: {e}"


# validator: exact error-reply form
check(kind({"id": 5, "error": "x", "code": "bad_snapshot"}) == "error", "{id,error,code} with a known code is an error reply")
check(kind({"id": 5, "error": "x"}).startswith("failure"), "{id,error} without code is a protocol violation")
check(kind({"id": 5, "error": "x", "code": "oops"}).startswith("failure"), "unknown code is a protocol violation")
check(kind({"id": 5, "error": "x", "code": "bad_request", "extra": 1}).startswith("failure"), "extra key in an error reply")
check(kind({"id": None, "error": "x", "code": "parse_error"}) == "error", "id null with parse_error (request not read)")
check(kind({"id": None, "error": "x", "code": "bad_request"}).startswith("failure"), "id null with bad_request")
check(kind({"id": 6, "error": "x", "code": "bad_request"}).startswith("failure"), "error reply with a foreign id")

# real engine
pool = EnginePool(engine, doc, 1, 10.0, Faults())
try:
    try:
        pool.call("u", {"op": "step", "snapshot": {"at": "menu", "bogus": 1}, "event": "btn:x"})
        check(False, "invalid snapshot: engine error expected")
    except EngineReplyError as e:
        check(e.code == "bad_snapshot", f"invalid snapshot → EngineReplyError code {e.code!r} (host repairs on bad_snapshot)")
    except EngineFailure as e:
        check(False, f"invalid snapshot → transport failure instead of an engine error: {e}")
    try:
        pool.call("u", {"op": "step", "snapshot": {"at": "menu"}, "event": "btn:x", "gaurds": {}})
        check(False, "unknown request key: engine error expected")
    except EngineReplyError as e:
        check(e.code == "bad_request", f"unknown request key → code {e.code!r}")
    r = pool.call("u", {"op": "start", "seq": 41})
    check(r["snapshot"].get("seq") == 42, f"start with seq 41 → snapshot seq {r['snapshot'].get('seq')} (42 expected)")
    r = pool.call("u", {"op": "start"})
    check(r["snapshot"].get("seq") == 1, f"start without seq → seq {r['snapshot'].get('seq')} (1: new user only)")
finally:
    pool.close()
print(f"host-python protocol v1: {fails} failure(s)")
sys.exit(1 if fails else 0)

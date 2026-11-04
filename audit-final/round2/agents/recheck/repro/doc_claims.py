#!/usr/bin/env python3
"""Probes of the rules added to docs/format.md and docs/host-contract.md after the first audit
(7239ab7..598fec6), against figram-engine with examples/features.bot.json.

  python3 -I doc_claims.py ENGINE DOC [--expect-red]

Each case: a request, a predicate on the reply, the prose it checks. Prints one line per case
and "claims N, failed M". With --expect-red the predicates are inverted (negative control:
every case must then fail, so a predicate that is always true is caught)."""
import json
import subprocess
import sys

ENGINE, DOC = sys.argv[1], sys.argv[2]
RED = "--expect-red" in sys.argv[3:]

cases = []


def case(name, req, pred, prose, raw=None):
    # a raw request is sent byte for byte (with the id appended), a dict is serialized with its id
    cases.append((name, raw if raw is not None else req, pred, prose))


def err(code):
    return lambda r: r.get("error", {}).get("code") == code if isinstance(r.get("error"), dict) else \
        r.get("error") == code or (isinstance(r.get("error"), str) and r["error"] == code) or r.get("code") == code


snap = lambda **k: dict({"at": "item", "seq": 5}, **k)

# format.md §8 requests: an event with a reserved prefix that is not well formed is bad_request,
# with or without token
for ev in ["timer:", "call:x", "call::ok", "call:a:", "call:"]:
    for tok in (None, 3):
        req = {"op": "step", "snapshot": snap(), "event": ev}
        if tok is not None:
            req["token"] = tok
        case(f"step {ev!r} token={tok}", req, err("bad_request"), "format.md §8: reserved prefix not of the form → bad_request")
# start rejects outcome events (host-contract §1 step 2: an outcome event for a user without a snapshot)
case("start call:check_stock:yes", {"op": "start", "event": "call:check_stock:yes"}, err("bad_request"),
     "host-contract §1 step 2 / format.md §8: start rejects outcome events")
case("start call:check_stock:yes token", {"op": "start", "event": "call:check_stock:yes", "token": 1},
     err("bad_request"), "format.md §8: start takes no token")
# rule 1: recovery keeps the waits; an accepted outcome is processed at the initial message
case("recovery outcome accepted removes wait",
     {"op": "step", "snapshot": {"at": "gone", "seq": 5, "pending": [{"call": "ping", "token": 4}, {"call": "check_stock", "token": 3}]},
      "event": "call:ping:pong", "token": 4},
     lambda r: r.get("recovered") is True and [w["call"] for w in r["snapshot"].get("pending", [])] == ["check_stock"]
     and any(a.get("call") == "log" for a in r["actions"]),
     "format.md §8 rule 1: recovery keeps waits, an accepted outcome is processed (root arrow call:ping:pong → log)")
case("recovery outcome stale chooses nothing, waits kept",
     {"op": "step", "snapshot": {"at": "gone", "seq": 5, "pending": [{"call": "check_stock", "token": 3}]},
      "event": "call:ping:pong", "token": 4},
     lambda r: r.get("recovered") is True and r["snapshot"].get("pending") == [{"call": "check_stock", "token": 3}]
     and not any(a.get("call") for a in r["actions"]),
     "format.md §8 rule 1: an outcome without a wait chooses nothing in recovery")
# rule 2: any outcome name is accepted with the token of a wait; an undeclared one only removes the wait
case("undeclared outcome removes the wait only",
     {"op": "step", "snapshot": snap(pending=[{"call": "check_stock", "token": 3}]), "event": "call:check_stock:maybe", "token": 3},
     lambda r: "pending" not in r.get("snapshot", {"pending": 1}) and r["actions"] == [] and r["snapshot"]["seq"] == 5
     and r["snapshot"]["at"] == "item",
     "format.md §8 rule 2, host-contract §2: an undeclared outcome only removes the wait (seq unchanged)")
# host-contract §1 step 4: store the snapshot even when seq did not change
case("accepted outcome without arrow: seq same, pending changed",
     {"op": "step", "snapshot": {"at": "home", "seq": 5, "timers": {"idle": 5}, "pending": [{"call": "check_stock", "token": 3}]},
      "event": "call:check_stock:yes", "token": 3},
     lambda r: r["snapshot"]["seq"] == 5 and "pending" not in r["snapshot"] and r["actions"] == [],
     "host-contract §1 step 4: an accepted outcome without an arrow removes its wait without a transition")
# format.md §7 snapshot: a wait token greater than seq is accepted
case("wait token > seq accepted",
     {"op": "step", "snapshot": snap(pending=[{"call": "check_stock", "token": 99}]), "event": "call:check_stock:yes", "token": 99},
     lambda r: r.get("snapshot", {}).get("at") == "item2",
     "format.md (snapshot): a snapshot with a token greater than seq is accepted")
# snapshot text: duplicate keys / lone surrogate / exponent out of range → parse_error
case("snapshot duplicate key", None, err("parse_error"), "format.md (snapshot): no duplicate keys → parse_error",
     raw='{"op":"step","snapshot":{"at":"item","at":"home","seq":5},"event":"btn:x"}')
case("snapshot lone surrogate", None, err("parse_error"), "format.md (snapshot): no lone surrogates → parse_error",
     raw='{"op":"step","snapshot":{"at":"item","seq":5,"resume":{"s":"\\ud800"}},"event":"btn:x"}')
case("snapshot exponent out of range", None, err("parse_error"), "format.md (snapshot): exponents in range → parse_error",
     raw='{"op":"step","snapshot":{"at":"item","seq":5e99999999999},"event":"btn:x"}')
# host-contract §3: a firing handled without a move removes the timer; leaving later still cancels it
case("consumed timer still cancelled on leave",
     {"op": "step", "snapshot": {"at": "vip", "seq": 7, "timers": {}}, "event": "cmd:start"},
     lambda r: {"cancel_timer": "nudge"} in r["actions"],
     "host-contract §3: leaving a message emits cancel_timer for a timer already consumed by a stay")
case("stay timer firing consumes timer",
     {"op": "step", "snapshot": {"at": "vip", "seq": 7, "timers": {"nudge": 6}}, "event": "timer:nudge", "token": 6},
     lambda r: "timers" not in r["snapshot"] and r["snapshot"]["seq"] == 8 and r["snapshot"]["at"] == "vip",
     "host-contract §3: a firing handled by an arrow without a target removes the timer")
# a duplicate wait is one wait: its outcome removes both
case("duplicate wait removed by one outcome",
     {"op": "step", "snapshot": snap(pending=[{"call": "ping", "token": 3}, {"call": "ping", "token": 3}]),
      "event": "call:ping:pong", "token": 3},
     lambda r: "pending" not in r["snapshot"],
     "format.md (snapshot): the same wait listed twice is one wait")

proc = subprocess.Popen([ENGINE, DOC], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
lines = []
for i, (_, req, _, _) in enumerate(cases):
    lines.append(raw_req[:-1] + f',"id":{i}}}' if isinstance(raw_req := req, str) else json.dumps(dict(req, id=i)))
out, _ = proc.communicate("\n".join(lines) + "\n", timeout=60)
replies = [json.loads(l) for l in out.splitlines()]
failed = 0
for (name, raw, pred, prose), r in zip(cases, replies):
    try:
        ok = bool(pred(r))
    except Exception as e:  # noqa: BLE001
        ok = False
    if RED:
        ok = not ok
    failed += not ok
    print(("ok   " if ok else "FAIL ") + name + " — " + prose + ("" if ok else f"\n     reply: {json.dumps(r)[:300]}"))
print(f"claims {len(cases)}, replies {len(replies)}, failed {failed}")
sys.exit(0 if failed == 0 and len(replies) == len(cases) else 1)

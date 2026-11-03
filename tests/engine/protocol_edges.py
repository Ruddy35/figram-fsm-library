"""Edge cases of the engine protocol that the scenarios do not cover
(docs/format.md, section 8; docs/host-contract.md, section 4).

The `figram trace` scenarios and the golden scenarios never send `restart`, a
partial set of conditions, non-boolean conditions, a timer or outcome event
without a token or a malformed `call:` event, and do not check the version in
`hello`. Mutation testing
(`Figram/Mutate.lean`) corrupts only the tables of the machine and does not touch
these places. The expectations are written by hand from docs/format.md
(section 7, "Decisions of the specification", and section 8), not taken from the
engine.

Parts:
* JSON requests (the `id` field is the case number): result, `need`, errors with
  a machine code (`code`), strict request fields, token and `seq` limits,
  `start` with `seq` and with a first event, "back to the previous section";
  protocol 3: outcome events (`call:<action>:<outcome>` with the token of a wait:
  accepted, stale, without an arrow, `every`, recovery, `restart`), malformed
  `call:` names, `start` with an outcome event, `pending` decoding errors
  (`bad_snapshot`), the `token` and `detached` fields of `call` in replies, arrows
  without a target (decision 13: no move, a timer consumed);
* raw byte lines in one process: `id` echo on a parse error, invalid UTF-8,
  blank lines without a reply, the request size limit (1 MiB by default),
  replies strictly in request order;
* `--max-request`: a small limit and 0 (no limit);
* the command line: mutants only with FIGRAM_ALLOW_MUTANTS=1, the `mutant` field
  in `hello`, an unknown mutation and wrong arguments are refused;
* the validators of clients/python/protocol.py on hand-written replies of protocol 3
  (`hello` of another version, `pending`, the `token` and `detached` fields of `call`).

    python3 protocol_edges.py ENGINE examples/features.bot.json [--json-only]

`--json-only` runs only the JSON requests (the harness self-test runs it through
proxies that corrupt replies line by line).
"""

import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "clients", "python"))
from protocol import (ChildTimeout, ProtocolError, check_error, check_reply, default_timeout,
                      mutant_env, run_child)

M = 9007199254740991          # 2^53 − 1, the upper limit of seq and tokens
MIB = 1048576                 # default request size limit
ITEM = {"at": "item", "resume": {"shop": "item"}, "timers": {"remind": 3}, "seq": 5}
IDLE = {"start_timer": "idle", "ms": 60000}
NUDGE = {"start_timer": "nudge", "ms": 30000}
FIRED = {"snapshot": {"at": "reminder", "back": "item", "resume": {"shop": "reminder"}, "seq": 6},
         "actions": [{"cancel_timer": "remind"}, {"send": "reminder"}]}
IGNORED = {"snapshot": ITEM, "actions": []}
HELLO = {"protocol": 3, "states": 8}
H = {"at": "home"}
LOG = {"call": "log", "detached": True}           # a background action without outcomes


def W(call, token):
    """A wait for an outcome in `pending` (docs/format.md, section 7, decision 12)."""
    return {"call": call, "token": token}


# The item message waiting for the outcome of check_stock called at seq 5.
PITEM = {**ITEM, "pending": [W("check_stock", 5)]}


def err(code):
    """Expectation "an error with this code" (the text for humans is not pinned)."""
    return ("error", code)


def step(snapshot, event, **kw):
    return {"op": "step", "snapshot": snapshot, "event": event, **kw}


def started(seq):
    return {"snapshot": {"at": "home", "timers": {"idle": seq}, "seq": seq},
            "actions": [{**IDLE, "token": seq}, {"send": "home"}]}


CASES = [
    ("hello: protocol version 3",
     {"op": "hello"}, HELLO),
    ("restart near the limit: seq continues up to 2^53−1, timers cancelled, places cleared",
     {"op": "restart", "snapshot": {"at": "item", "resume": {"shop": "item"}, "timers": {"remind": 7}, "seq": M - 1}},
     {"snapshot": {"at": "home", "timers": {"idle": M}, "seq": M},
      "actions": [{"cancel_timer": "remind"}, {**IDLE, "token": M}, {"send": "home"}]}),
    ("restart at the limit: seq_overflow without a snapshot, code seq_overflow",
     {"op": "restart", "snapshot": {"at": "item", "seq": M}}, {"error": "seq_overflow", "code": "seq_overflow"}),
    ("step at the limit: seq_overflow without a snapshot, code seq_overflow",
     step({"at": "home", "seq": M}, "btn:self"), {"error": "seq_overflow", "code": "seq_overflow"}),
    ("restart clears the remembered places",
     {"op": "restart", "snapshot": {"at": "home", "resume": {"shop": "sub"}}}, started(1)),
    ("a partial set of conditions: the missing one is asked for",
     step(H, "btn:go", guards={"other": True}), {"need": ["is_vip"]}),
    ("an extra condition does no harm",
     step(H, "btn:go", guards={"is_vip": True, "other": False}),
     {"snapshot": {"at": "vip", "timers": {"nudge": 1}, "seq": 1},
      "actions": [{"cancel_timer": "idle"}, {**NUDGE, "token": 1}, {"send": "vip"}]}),
    ("leaving a message cancels its timers, then the snapshot's other timers (leftovers of an "
     "older document version; format.md section 7, decision 2)",
     step({"at": "home", "timers": {"old": 2, "idle": 1}, "seq": 2}, "btn:go", guards={"is_vip": True}),
     {"snapshot": {"at": "vip", "timers": {"nudge": 3}, "seq": 3},
      "actions": [{"cancel_timer": "idle"}, {"cancel_timer": "old"}, {**NUDGE, "token": 3}, {"send": "vip"}]}),
    ("a non-boolean condition value — bad_request",
     step(H, "btn:go", guards={"is_vip": 1}), err("bad_request")),
    ("'guards' is not an object — bad_request",
     step(H, "btn:go", guards=None), err("bad_request")),
    ("timer event without a token — bad_request",
     step(ITEM, "timer:remind"), err("bad_request")),
    ("a stale token is ignored",
     step(ITEM, "timer:remind", token=2), IGNORED),
    ("the current token fires",
     step(ITEM, "timer:remind", token=3), FIRED),
    ("removed message: recovery with the recovered flag",
     step({"at": "gone", "timers": {"old": 4}, "seq": 9}, "btn:x"),
     {"snapshot": {"at": "home", "timers": {"idle": 10}, "seq": 10},
      "actions": [{"cancel_timer": "old"}, {**IDLE, "token": 10}, {"send": "home"}], "recovered": True}),
    ("removed message, an event of the initial message that needs a condition: need",
     step({"at": "gone", "timers": {"old": 4}, "seq": 9}, "btn:go"), {"need": ["is_vip"]}),
    ("removed message, the event processed as at the initial message: old timers cancelled, places kept",
     step({"at": "gone", "resume": {"shop": "item"}, "timers": {"old": 4}, "seq": 9}, "btn:go", guards={"is_vip": True}),
     {"snapshot": {"at": "vip", "resume": {"shop": "item"}, "timers": {"nudge": 10}, "seq": 10},
      "actions": [{"cancel_timer": "old"}, {**NUDGE, "token": 10}, {"send": "vip"}], "recovered": True}),
    ("removed message, a timer event: the plain recovery",
     step({"at": "gone", "timers": {"old": 4}, "seq": 9}, "timer:old", token=4),
     {"snapshot": {"at": "home", "timers": {"idle": 10}, "seq": 10},
      "actions": [{"cancel_timer": "old"}, {**IDLE, "token": 10}, {"send": "home"}], "recovered": True}),
    # ---- token: an integer 0…2^53−1 by value -----------------------------
    ("token 3.0 — the same integer 3", step(ITEM, "timer:remind", token=3.0), FIRED),
    ("token 2^53−1 is allowed (stale, ignored)", step(ITEM, "timer:remind", token=M), IGNORED),
    ("token 2^53 — bad_request", step(ITEM, "timer:remind", token=M + 1), err("bad_request")),
    ("token 2^64 — bad_request", step(ITEM, "timer:remind", token=2 ** 64), err("bad_request")),
    ("token −1 — bad_request", step(ITEM, "timer:remind", token=-1), err("bad_request")),
    ("token 1.5 — bad_request", step(ITEM, "timer:remind", token=1.5), err("bad_request")),
    ("token as a string — bad_request", step(ITEM, "timer:remind", token="3"), err("bad_request")),
    ("token true — bad_request", step(ITEM, "timer:remind", token=True), err("bad_request")),
    ("token null — bad_request", step(ITEM, "timer:remind", token=None), err("bad_request")),
    ("token on an ordinary event — bad_request", step(ITEM, "btn:other", token=3), err("bad_request")),
    # ---- event ------------------------------------------------------------
    ("empty event — bad_request", step(H, ""), err("bad_request")),
    ("'timer:' without a timer name — bad_request", step(ITEM, "timer:", token=3), err("bad_request")),
    ("event as a number — bad_request", step(H, 5), err("bad_request")),
    ("event null — bad_request", step(H, None), err("bad_request")),
    ("no event — bad_request", {"op": "step", "snapshot": H}, err("bad_request")),
    ("an event without an arrow is ignored", step(H, "btn:nothing"), {"snapshot": H, "actions": []}),
    # ---- strict request fields --------------------------------------------
    ("hello with an extra field — bad_request", {"op": "hello", "extra": 1}, err("bad_request")),
    ("start with a snapshot — bad_request", {"op": "start", "snapshot": H}, err("bad_request")),
    ("start with conditions — bad_request", {"op": "start", "guards": {}}, err("bad_request")),
    ("restart with an event — bad_request", {"op": "restart", "snapshot": H, "event": "btn:x"}, err("bad_request")),
    ("typo 'gaurds' — bad_request, not an endless need",
     step(H, "btn:go", gaurds={"is_vip": True}), err("bad_request")),
    ("field 'Token' (case) — bad_request", step(ITEM, "timer:remind", Token=3), err("bad_request")),
    ("unknown operation — bad_request", {"op": "HELLO"}, err("bad_request")),
    ("no operation — bad_request", {}, err("bad_request")),
    ("operation as a number — bad_request", {"op": 5}, err("bad_request")),
    # ---- snapshot ---------------------------------------------------------
    ("invalid step snapshot — bad_snapshot", step({"at": ""}, "btn:self"), err("bad_snapshot")),
    ("snapshot null — bad_snapshot", step(None, "btn:self"), err("bad_snapshot")),
    ("a snapshot of a newer version in restart — bad_snapshot",
     {"op": "restart", "snapshot": {"at": "home", "v": 2}}, err("bad_snapshot")),
    ("step without a snapshot — bad_request", {"op": "step", "event": "btn:self"}, err("bad_request")),
    ("restart without a snapshot — bad_request", {"op": "restart"}, err("bad_request")),
    # ---- start with seq ---------------------------------------------------
    ("start without seq: the first transition gets seq 1", {"op": "start"}, started(1)),
    ("start with seq 0 — as without seq", {"op": "start", "seq": 0}, started(1)),
    ("start with seq 41: the first transition gets seq 42", {"op": "start", "seq": 41}, started(42)),
    ("start with seq 2^53−2: seq 2^53−1", {"op": "start", "seq": M - 1}, started(M)),
    ("start with seq 2^53−1 — seq_overflow", {"op": "start", "seq": M}, {"error": "seq_overflow", "code": "seq_overflow"}),
    ("start with seq 2^53 — bad_request", {"op": "start", "seq": M + 1}, err("bad_request")),
    ("start with seq −1 — bad_request", {"op": "start", "seq": -1}, err("bad_request")),
    ("start with seq 1.5 — bad_request", {"op": "start", "seq": 1.5}, err("bad_request")),
    ("start with seq as a string — bad_request", {"op": "start", "seq": "1"}, err("bad_request")),
    ("start with seq 7.0 — the same integer 7", {"op": "start", "seq": 7.0}, started(8)),
    # ---- start with a first event: as if the user stood on the initial message ----
    ("start with an event that needs a condition: need",
     {"op": "start", "event": "btn:go"}, {"need": ["is_vip"]}),
    ("start with an event, condition true: straight to the target, home neither sent nor its timer started",
     {"op": "start", "event": "btn:go", "guards": {"is_vip": True}},
     {"snapshot": {"at": "vip", "timers": {"nudge": 1}, "seq": 1}, "actions": [{**NUDGE, "token": 1}, {"send": "vip"}]}),
    ("start with an event, condition false, seq 41: the next variant, seq 42",
     {"op": "start", "event": "btn:go", "guards": {"is_vip": False}, "seq": 41},
     {"snapshot": {"at": "catalog", "resume": {"shop": "catalog"}, "seq": 42}, "actions": [{"send": "catalog"}]}),
    ("start with an event, partial conditions: the missing one is asked for",
     {"op": "start", "event": "btn:go", "guards": {"other": True}}, {"need": ["is_vip"]}),
    ("start with an event into the initial message: exactly start (home sent once)",
     {"op": "start", "event": "cmd:start"}, started(1)),
    ("start with an event of the root section's arrow",
     {"op": "start", "event": "cmd:help"}, {"snapshot": {"at": "help", "seq": 1}, "actions": [{"send": "help"}]}),
    ("start with an event without an arrow: the plain start",
     {"op": "start", "event": "btn:nothing", "seq": 4}, started(5)),
    ("start with an event and seq 2^53−1 — seq_overflow",
     {"op": "start", "event": "cmd:help", "seq": M}, {"error": "seq_overflow", "code": "seq_overflow"}),
    ("start with an event that needs a condition and seq 2^53−1: need first",
     {"op": "start", "event": "btn:go", "seq": M}, {"need": ["is_vip"]}),
    ("start with a timer event — bad_request", {"op": "start", "event": "timer:idle"}, err("bad_request")),
    ("start with an empty event — bad_request", {"op": "start", "event": ""}, err("bad_request")),
    ("start with an event as a number — bad_request", {"op": "start", "event": 5}, err("bad_request")),
    ("start with an event and a token — bad_request",
     {"op": "start", "event": "btn:go", "token": 1}, err("bad_request")),
    ("start with an event and a non-boolean condition — bad_request",
     {"op": "start", "event": "btn:go", "guards": {"is_vip": "yes"}}, err("bad_request")),
    # ---- "back to the previous section" (decision 10) ----------------------
    ("back: a move into another section remembers the message left",
     step(H, "btn:go", guards={"is_vip": False}),
     {"snapshot": {"at": "catalog", "back": "home", "resume": {"shop": "catalog"}, "seq": 1},
      "actions": [{"cancel_timer": "idle"}, {"send": "catalog"}]}),
    ("back: a move inside a section keeps the place",
     step({**ITEM, "back": "catalog"}, "btn:other"),
     {"snapshot": {"at": "item2", "back": "catalog", "resume": {"shop": "item2"}, "seq": 6},
      "actions": [{"cancel_timer": "remind"}, {"send": "item2"}]}),
    ("back: leads to the place and remembers the message left",
     step({"at": "help", "back": "item", "seq": 4}, "btn:prev"),
     {"snapshot": {"at": "item", "back": "help", "resume": {"shop": "item"}, "timers": {"remind": 5}, "seq": 5},
      "actions": [{"start_timer": "remind", "ms": 5000, "token": 5}, {"send": "item"}]}),
    ("back without a place: the fallback `to`",
     step({"at": "help", "seq": 4}, "btn:prev"),
     {"snapshot": {"at": "home", "timers": {"idle": 5}, "seq": 5}, "actions": [{**IDLE, "token": 5}, {"send": "home"}]}),
    ("back to a message no longer in the bot: the fallback, the place kept",
     step({"at": "help", "back": "gone", "seq": 4}, "btn:prev"),
     {"snapshot": {"at": "home", "back": "gone", "timers": {"idle": 5}, "seq": 5},
      "actions": [{**IDLE, "token": 5}, {"send": "home"}]}),
    ("back with `resume`: without a place, resume the section",
     step({"at": "paid", "resume": {"shop": "item2"}, "seq": 4}, "btn:back"),
     {"snapshot": {"at": "item2", "back": "paid", "resume": {"shop": "item2"}, "seq": 5}, "actions": [{"send": "item2"}]}),
    ("back: start with an event leaves no message, so there is no place",
     {"op": "start", "event": "btn:go", "guards": {"is_vip": False}},
     {"snapshot": {"at": "catalog", "resume": {"shop": "catalog"}, "seq": 1}, "actions": [{"send": "catalog"}]}),
    ("back: recovery keeps the place and an event of the root uses it",
     step({"at": "gone", "back": "item2", "seq": 3}, "btn:prev"),
     {"snapshot": {"at": "item2", "back": "item2", "resume": {"shop": "item2"}, "seq": 4},
      "actions": [{"send": "item2"}], "recovered": True}),
    ("restart clears the place to go back to",
     {"op": "restart", "snapshot": {"at": "catalog", "back": "home"}}, started(1)),
    ("an ignored event keeps the place",
     step({"at": "home", "back": "item"}, "btn:nothing"), {"snapshot": {"at": "home", "back": "item"}, "actions": []}),
    ("back as an empty string — bad_snapshot", step({"at": "home", "back": ""}, "btn:self"), err("bad_snapshot")),
    ("back as a number — bad_snapshot", step({"at": "home", "back": 1}, "btn:self"), err("bad_snapshot")),
    ("back null — bad_snapshot", step({"at": "home", "back": None}, "btn:self"), err("bad_snapshot")),
    # ---- protocol 3: actions with outcomes (section 7, decision 12; section 8, rule 2) ----
    ("an action with outcomes: call carries the step's token, the wait is recorded; no move keeps timers and places",
     step(ITEM, "btn:check"),
     {"snapshot": {**ITEM, "pending": [W("check_stock", 6)], "seq": 6}, "actions": [{"call": "check_stock", "token": 6}]}),
    ("a background action: call carries detached true, no token, no wait",
     step(H, "btn:log"), {"snapshot": {"at": "home", "seq": 1}, "actions": [LOG]}),
    ("a background action with outcomes: call carries token and detached",
     step(H, "btn:ping"),
     {"snapshot": {"at": "home", "pending": [W("ping", 1)], "seq": 1},
      "actions": [{"call": "ping", "token": 1, "detached": True}]}),
    ("every: a new call adds its own wait, the newest first",
     step({"at": "home", "pending": [W("ping", 1)], "seq": 1}, "btn:ping"),
     {"snapshot": {"at": "home", "pending": [W("ping", 2), W("ping", 1)], "seq": 2},
      "actions": [{"call": "ping", "token": 2, "detached": True}]}),
    ("without every: a new call of the action replaces its wait",
     step(PITEM, "btn:check"),
     {"snapshot": {**ITEM, "pending": [W("check_stock", 6)], "seq": 6}, "actions": [{"call": "check_stock", "token": 6}]}),
    ("an outcome with the token of the wait: accepted, the wait removed, the arrow of the message taken",
     step(PITEM, "call:check_stock:yes", token=5),
     {"snapshot": {"at": "item2", "resume": {"shop": "item2"}, "seq": 6},
      "actions": [{"cancel_timer": "remind"}, {"send": "item2"}]}),
    ("an outcome that leads out of the section remembers the place to go back to",
     step(PITEM, "call:check_stock:no", token=5),
     {"snapshot": {"at": "catalog", "back": "item", "resume": {"shop": "catalog"}, "seq": 6},
      "actions": [{"cancel_timer": "remind"}, {"send": "catalog"}]}),
    ("the outcome failed: an arrow without a target with a background action, the timer stays",
     step(PITEM, "call:check_stock:failed", token=5), {"snapshot": {**ITEM, "seq": 6}, "actions": [LOG]}),
    ("an outcome with a stale token is ignored", step(PITEM, "call:check_stock:yes", token=4),
     {"snapshot": PITEM, "actions": []}),
    ("an outcome with the token of another action's wait is ignored", step(PITEM, "call:ping:pong", token=5),
     {"snapshot": PITEM, "actions": []}),
    ("an outcome without any wait is ignored", step(H, "call:check_stock:yes", token=0), {"snapshot": H, "actions": []}),
    ("an accepted outcome without an arrow is ignored, but its wait is gone (seq unchanged)",
     step({"at": "home", "pending": [W("check_stock", 5)], "seq": 5}, "call:check_stock:yes", token=5),
     {"snapshot": {"at": "home", "seq": 5}, "actions": []}),
    ("an undeclared outcome with the token of a wait: accepted, no arrow, the wait removed",
     step(PITEM, "call:check_stock:maybe", token=5), {"snapshot": {**ITEM, "seq": 5}, "actions": []}),
    ("an outcome handled by the root section wherever the user is",
     step({"at": "catalog", "pending": [W("ping", 2)], "seq": 4}, "call:ping:pong", token=2),
     {"snapshot": {"at": "catalog", "seq": 5}, "actions": [LOG]}),
    ("a wait of an action that is no longer in the document still accepts its outcome",
     step({"at": "home", "pending": [W("gone", 3)], "seq": 4}, "call:gone:ok", token=3),
     {"snapshot": {"at": "home", "seq": 4}, "actions": []}),
    ("an action name with ':': the event is split at the last ':'",
     step({"at": "home", "pending": [W("a:b", 3)], "seq": 4}, "call:a:b:c", token=3),
     {"snapshot": {"at": "home", "seq": 4}, "actions": []}),
    ("an action name with ':': the wait of `a` does not accept call:a:b:c",
     step({"at": "home", "pending": [W("a", 3)], "seq": 4}, "call:a:b:c", token=3),
     {"snapshot": {"at": "home", "pending": [W("a", 3)], "seq": 4}, "actions": []}),
    ("a move keeps the waits",
     step({"at": "home", "timers": {"idle": 3}, "pending": [W("check_stock", 2)], "seq": 3}, "btn:go",
          guards={"is_vip": True}),
     {"snapshot": {"at": "vip", "timers": {"nudge": 4}, "pending": [W("check_stock", 2)], "seq": 4},
      "actions": [{"cancel_timer": "idle"}, {**NUDGE, "token": 4}, {"send": "vip"}]}),
    ("every: the outcome of the older call is accepted, the newer wait kept",
     step({"at": "home", "pending": [W("ping", 7), W("ping", 6)], "seq": 7}, "call:ping:pong", token=6),
     {"snapshot": {"at": "home", "pending": [W("ping", 7)], "seq": 8}, "actions": [LOG]}),
    ("every: a repeated delivery of the same outcome is ignored",
     step({"at": "home", "pending": [W("ping", 7)], "seq": 8}, "call:ping:pong", token=6),
     {"snapshot": {"at": "home", "pending": [W("ping", 7)], "seq": 8}, "actions": []}),
    ("seq at the limit: an accepted outcome without an arrow makes no transition, no seq_overflow",
     step({"at": "home", "pending": [W("check_stock", 5)], "seq": M}, "call:check_stock:yes", token=5),
     {"snapshot": {"at": "home", "seq": M}, "actions": []}),
    ("seq at the limit: an outcome with an arrow — seq_overflow",
     step({"at": "home", "pending": [W("ping", 5)], "seq": M}, "call:ping:pong", token=5),
     {"error": "seq_overflow", "code": "seq_overflow"}),
    # ---- the token of an outcome event: required, an integer 0…2^53−1 by value ----
    ("outcome event without a token — bad_request", step(PITEM, "call:check_stock:yes"), err("bad_request")),
    ("outcome token 5.0 — the same integer 5", step(PITEM, "call:check_stock:yes", token=5.0),
     {"snapshot": {"at": "item2", "resume": {"shop": "item2"}, "seq": 6},
      "actions": [{"cancel_timer": "remind"}, {"send": "item2"}]}),
    ("outcome token 2^53−1 is allowed (no wait, ignored)", step(PITEM, "call:check_stock:yes", token=M),
     {"snapshot": PITEM, "actions": []}),
    ("outcome token 2^53 — bad_request", step(PITEM, "call:check_stock:yes", token=M + 1), err("bad_request")),
    ("outcome token −1 — bad_request", step(PITEM, "call:check_stock:yes", token=-1), err("bad_request")),
    ("outcome token 1.5 — bad_request", step(PITEM, "call:check_stock:yes", token=1.5), err("bad_request")),
    ("outcome token as a string — bad_request", step(PITEM, "call:check_stock:yes", token="5"), err("bad_request")),
    ("outcome token true — bad_request", step(PITEM, "call:check_stock:yes", token=True), err("bad_request")),
    ("outcome token null — bad_request", step(PITEM, "call:check_stock:yes", token=None), err("bad_request")),
    # ---- malformed `call:` names (section 4: split at the last ':', both parts non-empty) ----
    ("'call:' alone — bad_request", step(H, "call:", token=1), err("bad_request")),
    ("'call:x' without an outcome — bad_request", step(H, "call:check_stock", token=1), err("bad_request")),
    ("'call:x:' with an empty outcome — bad_request", step(H, "call:check_stock:", token=1), err("bad_request")),
    ("'call::y' with an empty action — bad_request", step(H, "call::yes", token=1), err("bad_request")),
    ("'call:x' without a token — bad_request", step(H, "call:check_stock"), err("bad_request")),
    ("'CALL:x:y' is an ordinary event: a token — bad_request", step(H, "CALL:check_stock:yes", token=1),
     err("bad_request")),
    ("'CALL:x:y' without a token: an ordinary event without an arrow, ignored", step(H, "CALL:check_stock:yes"),
     {"snapshot": H, "actions": []}),
    ("field 'Token' (case) on an outcome event — bad_request", step(PITEM, "call:check_stock:yes", Token=5),
     err("bad_request")),
    # ---- start with an outcome event: a new user has no waits ---------------------
    ("start with an outcome event — bad_request", {"op": "start", "event": "call:ping:pong"}, err("bad_request")),
    ("start with an outcome event and seq — bad_request",
     {"op": "start", "event": "call:check_stock:yes", "seq": 5}, err("bad_request")),
    ("start with an outcome event and a token — bad_request",
     {"op": "start", "event": "call:ping:pong", "token": 1}, err("bad_request")),
    ("start with a malformed call: event — bad_request", {"op": "start", "event": "call:x"}, err("bad_request")),
    ("start with the event of an arrow without a target: its actions, then the initial message",
     {"op": "start", "event": "btn:ping"},
     {"snapshot": {"at": "home", "timers": {"idle": 1}, "pending": [W("ping", 1)], "seq": 1},
      "actions": [{"call": "ping", "token": 1, "detached": True}, {**IDLE, "token": 1}, {"send": "home"}]}),
    # ---- pending in the snapshot: decoding errors are bad_snapshot -----------------
    ("pending not an array — bad_snapshot", step({"at": "home", "pending": {"ping": 1}}, "btn:self"), err("bad_snapshot")),
    ("pending null — bad_snapshot", step({"at": "home", "pending": None}, "btn:self"), err("bad_snapshot")),
    ("a pending entry without a token — bad_snapshot", step({"at": "home", "pending": [{"call": "ping"}]}, "btn:self"),
     err("bad_snapshot")),
    ("a pending entry without a call — bad_snapshot", step({"at": "home", "pending": [{"token": 1}]}, "btn:self"),
     err("bad_snapshot")),
    ("a pending entry with an empty call — bad_snapshot", step({"at": "home", "pending": [W("", 1)]}, "btn:self"),
     err("bad_snapshot")),
    ("a pending entry with a call as a number — bad_snapshot",
     step({"at": "home", "pending": [{"call": 1, "token": 1}]}, "btn:self"), err("bad_snapshot")),
    ("a pending token −1 — bad_snapshot", step({"at": "home", "pending": [W("ping", -1)]}, "btn:self"),
     err("bad_snapshot")),
    ("a pending token 1.5 — bad_snapshot", step({"at": "home", "pending": [W("ping", 1.5)]}, "btn:self"),
     err("bad_snapshot")),
    ("a pending token 2^53 — bad_snapshot", step({"at": "home", "pending": [W("ping", M + 1)]}, "btn:self"),
     err("bad_snapshot")),
    ("a pending token as a string — bad_snapshot", step({"at": "home", "pending": [W("ping", "1")]}, "btn:self"),
     err("bad_snapshot")),
    ("a pending entry with an extra field — bad_snapshot",
     step({"at": "home", "pending": [{"call": "ping", "token": 1, "outcome": "pong"}]}, "btn:self"), err("bad_snapshot")),
    ("a pending entry as an array — bad_snapshot", step({"at": "home", "pending": [["ping", 1]]}, "btn:self"),
     err("bad_snapshot")),
    ("a bad pending in restart — bad_snapshot", {"op": "restart", "snapshot": {"at": "home", "pending": [{"call": "ping"}]}},
     err("bad_snapshot")),
    ("a bad pending with a malformed outcome event: the snapshot is checked first — bad_snapshot",
     step({"at": "home", "pending": [W("", 1)]}, "call:x"), err("bad_snapshot")),
    ("pending token 1.0 — the same integer 1, written as 1",
     step({"at": "home", "pending": [W("ping", 1.0)]}, "btn:nothing"),
     {"snapshot": {"at": "home", "pending": [W("ping", 1)]}, "actions": []}),
    ("an empty pending is the default and is not written", step({"at": "home", "pending": []}, "btn:nothing"),
     {"snapshot": H, "actions": []}),
    # ---- restart and recovery -------------------------------------------------------
    ("restart clears the waits", {"op": "restart", "snapshot": PITEM},
     {"snapshot": {"at": "home", "timers": {"idle": 6}, "seq": 6},
      "actions": [{"cancel_timer": "remind"}, {**IDLE, "token": 6}, {"send": "home"}]}),
    ("recovered step with an accepted outcome: the wait removed, the root's arrow without a target, then the "
     "initial message",
     step({"at": "gone", "timers": {"old": 4}, "pending": [W("ping", 6), W("check_stock", 5)], "seq": 6},
          "call:ping:pong", token=6),
     {"snapshot": {"at": "home", "timers": {"idle": 7}, "pending": [W("check_stock", 5)], "seq": 7},
      "actions": [{"cancel_timer": "old"}, LOG, {**IDLE, "token": 7}, {"send": "home"}], "recovered": True}),
    ("recovered step with an accepted outcome without an arrow at the initial message: the plain start over, "
     "the wait removed",
     step({"at": "gone", "pending": [W("check_stock", 5)], "seq": 6}, "call:check_stock:yes", token=5),
     {"snapshot": {"at": "home", "timers": {"idle": 7}, "seq": 7},
      "actions": [{**IDLE, "token": 7}, {"send": "home"}], "recovered": True}),
    ("recovered step with an outcome that is not accepted: the plain start over, the waits kept",
     step({"at": "gone", "pending": [W("ping", 5)], "seq": 6}, "call:ping:pong", token=4),
     {"snapshot": {"at": "home", "timers": {"idle": 7}, "pending": [W("ping", 5)], "seq": 7},
      "actions": [{**IDLE, "token": 7}, {"send": "home"}], "recovered": True}),
    ("recovered step with an ordinary event keeps the waits",
     step({"at": "gone", "pending": [W("check_stock", 5)], "seq": 6}, "btn:nothing"),
     {"snapshot": {"at": "home", "timers": {"idle": 7}, "pending": [W("check_stock", 5)], "seq": 7},
      "actions": [{**IDLE, "token": 7}, {"send": "home"}], "recovered": True}),
    ("recovered step with the event of an arrow without a target: its actions, then the initial message",
     step({"at": "gone", "seq": 6}, "btn:ping"),
     {"snapshot": {"at": "home", "timers": {"idle": 7}, "pending": [W("ping", 7)], "seq": 7},
      "actions": [{"call": "ping", "token": 7, "detached": True}, {**IDLE, "token": 7}, {"send": "home"}],
      "recovered": True}),
    # ---- arrows without a target (decision 13) ------------------------------------
    ("an arrow without a target: actions only; no exit or entry, timers and places unchanged, seq + 1",
     step({"at": "home", "back": "item", "resume": {"shop": "item"}, "timers": {"idle": 3}, "seq": 3}, "btn:log"),
     {"snapshot": {"at": "home", "back": "item", "resume": {"shop": "item"}, "timers": {"idle": 3}, "seq": 4},
      "actions": [LOG]}),
    ("a timer firing handled by an arrow without a target consumes the timer",
     step({"at": "vip", "timers": {"nudge": 2}, "seq": 2}, "timer:nudge", token=2),
     {"snapshot": {"at": "vip", "seq": 3}, "actions": [LOG]}),
    ("a second firing of a consumed timer is ignored", step({"at": "vip", "seq": 3}, "timer:nudge", token=2),
     {"snapshot": {"at": "vip", "seq": 3}, "actions": []}),
    ("an arrow without a target and without actions: seq + 1 only",
     step({"at": "home", "pending": [W("ping", 2)], "seq": 3}, "call:ping:failed", token=2),
     {"snapshot": {"at": "home", "seq": 4}, "actions": []}),
    ("seq at the limit: an arrow without a target — seq_overflow", step({"at": "home", "seq": M}, "btn:log"),
     {"error": "seq_overflow", "code": "seq_overflow"}),
]

ITEM_TXT = json.dumps(ITEM).encode()
PITEM_TXT = json.dumps(PITEM).encode()
YES = {"snapshot": {"at": "item2", "resume": {"shop": "item2"}, "seq": 6},
       "actions": [{"cancel_timer": "remind"}, {"send": "item2"}]}
ZERO_TXT = b'{"at":"item","resume":{"shop":"item"},"timers":{"remind":0}}'
FIRED_ZERO = {"snapshot": {"at": "reminder", "back": "item", "resume": {"shop": "reminder"}, "seq": 1},
              "actions": [{"cancel_timer": "remind"}, {"send": "reminder"}]}


def pad_to(line: bytes, n: int) -> bytes:
    """Pad a request line with spaces (JSON whitespace) to n bytes."""
    assert len(line) <= n
    return line + b" " * (n - len(line))


# Raw lines: (what, the bytes of the line without \n, expectation). Expectation: dict — the
# whole reply (with id); ("error", id, code) — an error with this id and code; None — no reply.
RAW = [
    ("parse error (text after the JSON): id echo", b'{"id":100,"op":"hello"} x', ("error", 100, "parse_error")),
    ("duplicate key in the snapshot: id echo",
     b'{"id":101,"op":"step","snapshot":{"at":"home","at":"vip"},"event":"btn:self"}', ("error", 101, "parse_error")),
    ("nesting deeper than 256: id echo",
     b'{"id":102,"op":"step","guards":' + b"[" * 300 + b"]" * 300 + b"}", ("error", 102, "parse_error")),
    ("exponent above 1000: id echo", b'{"id":103,"op":"hello","x":1e5000}', ("error", 103, "parse_error")),
    ("not JSON: id null", b"hello", ("error", None, "parse_error")),
    ("echo of a string id after spaces", b'  {  "id" : "abc" , "op":', ("error", "abc", "parse_error")),
    ("echo of id true", b'{"id":true,"op":}', ("error", True, "parse_error")),
    ("echo of id null", b'{"id":null,"op":}', ("error", None, "parse_error")),
    ("id echo by value: -0 → 0", b'{"id":-0,"op":}', ("error", 0, "parse_error")),
    ("id echo by value: 1.5e3", b'{"id":1.5e3,"op":}', ("error", 1500, "parse_error")),
    ("an object id on a parse error: null", b'{"id":{"a":1},"op":}', ("error", None, "parse_error")),
    ("no ',' or '}' after the id: null", b'{"id":12x,"op":"hello"}', ("error", None, "parse_error")),
    ("id is not the first field: null", b'{"op":"hello","id":5', ("error", None, "parse_error")),
    ("invalid UTF-8 in the event: parse_error, id echo",
     b'{"id":104,"op":"step","snapshot":{"at":"home"},"event":"\xff"}', ("error", 104, "parse_error")),
    ("invalid UTF-8 in the id itself: null", b'{"id":"\xff","op":"hello"}', ("error", None, "parse_error")),
    ("CESU surrogate: parse_error", b'{"id":105,"op":"hello","x":"\xed\xa0\x80"}', ("error", 105, "parse_error")),
    ("overlong encoding of '/': parse_error", b'{"id":106,"op":"hello","x":"\xc0\xaf"}', ("error", 106, "parse_error")),
    ("code point above U+10FFFF: parse_error", b'{"id":107,"op":"hello","x":"\xf4\x90\x80\x80"}', ("error", 107, "parse_error")),
    ("truncated UTF-8 sequence: parse_error",
     b'{"id":108,"op":"hello","x":"\xe2\x82"}', ("error", 108, "parse_error")),
    ("empty line — no reply", b"", None),
    ("line of spaces, tabs and CR — no reply", b"  \t\r", None),
    ("BOM before the request: parse_error, id null", b'\xef\xbb\xbf{"id":109,"op":"hello"}', ("error", None, "parse_error")),
    ("CRLF: CR is JSON whitespace", b'{"id":110,"op":"hello"}\r', {"id": 110, **HELLO}),
    ("request is not an object: bad_request, id null", b"[1]", ("error", None, "bad_request")),
    ("token -0 = 0",
     b'{"id":111,"op":"step","snapshot":' + ZERO_TXT + b',"event":"timer:remind","token":-0}', {"id": 111, **FIRED_ZERO}),
    ("token 3e0 = 3",
     b'{"id":112,"op":"step","snapshot":' + ITEM_TXT + b',"event":"timer:remind","token":3e0}', {"id": 112, **FIRED}),
    ("token 30e-1 = 3",
     b'{"id":113,"op":"step","snapshot":' + ITEM_TXT + b',"event":"timer:remind","token":30e-1}', {"id": 113, **FIRED}),
    ("start with seq -0 — as without seq", b'{"id":114,"op":"start","seq":-0}', {"id": 114, **started(1)}),
    ("outcome token 50e-1 = 5",
     b'{"id":119,"op":"step","snapshot":' + PITEM_TXT + b',"event":"call:check_stock:yes","token":50e-1}',
     {"id": 119, **YES}),
    ("outcome token -0 = 0 (the wait of token 0)",
     b'{"id":120,"op":"step","snapshot":{"at":"home","pending":[{"call":"ping","token":0}]},"event":"call:ping:pong",'
     b'"token":-0}', {"id": 120, "snapshot": {"at": "home", "seq": 1}, "actions": [LOG]}),
    ("pending token 5e0 in the snapshot = 5",
     b'{"id":121,"op":"step","snapshot":{"at":"home","pending":[{"call":"ping","token":5e0}]},"event":"x"}',
     {"id": 121, "snapshot": {"at": "home", "pending": [W("ping", 5)]}, "actions": []}),
    ("exactly 1 MiB — processed", pad_to(b'{"id":115,"op":"hello"}', MIB), {"id": 115, **HELLO}),
    ("1 MiB + 1 byte — too_large, id echo", pad_to(b'{"id":116,"op":"hello"}', MIB + 1), ("error", 116, "too_large")),
    ("too large request without an id at the start — too_large, null",
     b'{"pad":"' + b"x" * MIB + b'"}', ("error", None, "too_large")),
    ("after too_large the process reads the next line", b'{"id":117,"op":"hello"}', {"id": 117, **HELLO}),
    ("line of spaces longer than the limit — no reply", b" " * (2 * MIB), None),
    ("last line without \\n is processed", b'{"id":118,"op":"hello"}', {"id": 118, **HELLO}),
]

LIMITED = [  # figram-engine --max-request 64
    ("--max-request 64: exactly 64 bytes", pad_to(b'{"id":200,"op":"hello"}', 64), {"id": 200, **HELLO}),
    ("--max-request 64: 65 bytes — too_large", pad_to(b'{"id":201,"op":"hello"}', 65), ("error", 201, "too_large")),
    ("--max-request 64: an id longer than the limit is still echoed",
     pad_to(b'{"id":"' + b"y" * 100 + b'","op":"hello"}', 200), ("error", "y" * 100, "too_large")),
    ("--max-request 64: the next line is processed", b'{"id":202,"op":"hello"}', {"id": 202, **HELLO}),
]

UNLIMITED = [  # figram-engine --max-request 0
    ("--max-request 0: a 3 MiB line is processed", pad_to(b'{"id":300,"op":"hello"}', 3 * MIB), {"id": 300, **HELLO}),
]

engine, doc = sys.argv[1:3]
json_only = sys.argv[3:] == ["--json-only"]
if sys.argv[3:] and not json_only:
    raise SystemExit("usage: protocol_edges.py ENGINE DOC [--json-only]")
bad = 0
answered = 0
total = 0


def fail(what, msg):
    global bad
    bad += 1
    print(f"FAIL {what}: {msg}")


def lines_of(out):
    lines = out.stdout.decode("utf-8").split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def same_id(got, exp) -> bool:
    """The reply id equals the expected one by value and by JSON type (true ≠ 1)."""
    if exp is None or type(exp) is bool:
        return got is exp
    if type(exp) is str:
        return type(got) is str and got == exp
    return type(got) in (int, float) and got == exp


# ---- JSON requests ------------------------------------------------------------
data = b"".join(json.dumps({**req, "id": i}).encode() + b"\n" for i, (_, req, _) in enumerate(CASES))
try:
    out = run_child([engine, doc], data, default_timeout(), "protocol edge cases")
except ChildTimeout as e:
    raise SystemExit(str(e))
lines = lines_of(out)
total += len(CASES)
if out.returncode != 0 or len(lines) != len(CASES):
    fail("JSON requests", f"replies {len(lines)} of {len(CASES)}, exit code {out.returncode}")
for i, ((what, req, exp), line) in enumerate(zip(CASES, lines)):
    answered += 1
    try:
        resp = json.loads(line)
        variant = check_reply(resp, i, req["op"] if type(req.get("op")) is str else "")
    except (ValueError, ProtocolError) as e:
        fail(what, f"reply violates the protocol: {e}: {line[:200]}")
        continue
    body = {k: v for k, v in resp.items() if k != "id"}
    ok = variant == "error" and resp["code"] == exp[1] if isinstance(exp, tuple) else body == exp
    if not ok:
        fail(what, f"{body} ≠ expected {exp}")


def run_raw(section, args, cases, final_newline=True):
    """Raw lines in one process; replies strictly in the order of the lines."""
    global answered, total
    total += len(cases)
    data = b"\n".join(line for _, line, _ in cases) + (b"\n" if final_newline else b"")
    try:
        out = run_child([engine] + args + [doc], data, default_timeout(), section)
    except ChildTimeout as e:
        raise SystemExit(str(e))
    lines = lines_of(out)
    expected = [c for c in cases if c[2] is not None]
    if out.returncode != 0 or len(lines) != len(expected):
        fail(section, f"replies {len(lines)}, expected {len(expected)}, exit code {out.returncode}: "
                      f"{out.stderr.decode(errors='replace')[:200]}")
        return
    answered += len(cases) - len(expected)      # the absence of replies to blank lines is checked
    for (what, _, exp), line in zip(expected, lines):
        answered += 1
        try:
            resp = json.loads(line)
            if isinstance(exp, tuple):
                code = check_error(resp)
                ok = same_id(resp["id"], exp[1]) and code == exp[2]
            else:
                check_reply(resp, exp["id"], "hello" if "protocol" in exp else "step")
                ok = resp == exp
        except (ValueError, ProtocolError) as e:
            fail(what, f"reply violates the protocol: {e}: {line[:200]}")
            continue
        if not ok:
            fail(what, f"{resp} ≠ expected {exp}")


# ---- the validators of clients/python/protocol.py on hand-written replies (protocol 3) ----
# (reply, op, accepted?): the same rows as TestFixCheckReplyProtocolChanges in clients/go.
R = '{"id":5,"snapshot":%s,"actions":%s}'
VALIDATOR = [
    ('{"id":5,"protocol":3,"states":-1}', "hello", False),
    ('{"id":5,"protocol":3,"states":18446744073709551616}', "hello", False),
    ('{"id":5,"protocol":3,"states":8}', "hello", True),
    ('{"id":5,"protocol":3,"states":8,"mutant":"drop-call:ping"}', "hello", True),
    ('{"id":5,"protocol":2,"states":8}', "hello", False),
    ('{"id":5,"protocol":4,"states":8}', "hello", False),
    ('{"id":5,"protocol":3.0,"states":8}', "hello", False),
    ('{"id":5,"protocol":"3","states":8}', "hello", False),
    (R % ('{"at":"item","pending":[{"call":"check_stock","token":6}],"seq":6}', '[]'), "step", True),
    (R % ('{"at":"home","pending":[{"call":"ping","token":7},{"call":"ping","token":6}],"seq":7}', '[]'), "step", True),
    (R % ('{"at":"home","pending":[]}', '[]'), "step", True),
    (R % ('{"at":"home","pending":[{"call":"ping"}]}', '[]'), "step", False),
    (R % ('{"at":"home","pending":[{"token":1}]}', '[]'), "step", False),
    (R % ('{"at":"home","pending":[{"call":"","token":1}]}', '[]'), "step", False),
    (R % ('{"at":"home","pending":[{"call":1,"token":1}]}', '[]'), "step", False),
    (R % ('{"at":"home","pending":[{"call":"ping","token":-1}]}', '[]'), "step", False),
    (R % ('{"at":"home","pending":[{"call":"ping","token":1.5}]}', '[]'), "step", False),
    (R % ('{"at":"home","pending":[{"call":"ping","token":1.0}]}', '[]'), "step", False),
    (R % ('{"at":"home","pending":[{"call":"ping","token":9007199254740992}]}', '[]'), "step", False),
    (R % ('{"at":"home","pending":[{"call":"ping","token":true}]}', '[]'), "step", False),
    (R % ('{"at":"home","pending":[{"call":"ping","token":1,"x":1}]}', '[]'), "step", False),
    (R % ('{"at":"home","pending":[["ping",1]]}', '[]'), "step", False),
    (R % ('{"at":"home","pending":{"ping":1}}', '[]'), "step", False),
    (R % ('{"at":"home","pending":null}', '[]'), "step", False),
    (R % ('{"at":"item","seq":6}', '[{"call":"check_stock","token":6}]'), "step", True),
    (R % ('{"at":"home","seq":1}', '[{"call":"log","detached":true}]'), "step", True),
    (R % ('{"at":"home","seq":1}', '[{"call":"ping","token":1,"detached":true}]'), "step", True),
    (R % ('{"at":"home","seq":1}', '[{"call":"log","detached":false}]'), "step", False),
    (R % ('{"at":"home","seq":1}', '[{"call":"log","detached":"true"}]'), "step", False),
    (R % ('{"at":"home","seq":1}', '[{"call":"log","detached":1}]'), "step", False),
    (R % ('{"at":"home","seq":1}', '[{"call":"log","detached":null}]'), "step", False),
    (R % ('{"at":"home","seq":1}', '[{"call":"ping","token":-1}]'), "step", False),
    (R % ('{"at":"home","seq":1}', '[{"call":"ping","token":1.5}]'), "step", False),
    (R % ('{"at":"home","seq":1}', '[{"call":"ping","token":"1"}]'), "step", False),
    (R % ('{"at":"home","seq":1}', '[{"call":"ping","token":true}]'), "step", False),
    (R % ('{"at":"home","seq":1}', '[{"call":"","token":1}]'), "step", False),
    (R % ('{"at":"home","seq":1}', '[{"call":"ping","token":1,"outcome":"pong"}]'), "step", False),
    (R % ('{"at":"home","seq":1}', '[{"call":"ping","ms":1,"token":1}]'), "step", False),
    (R % ('{"at":"home","seq":1}', '[{"send":"home","detached":true}]'), "step", False),
    (R % ('{"at":"home","seq":1}', '[{"start_timer":"t","ms":1,"token":1,"detached":true}]'), "step", False),
    (R % ('{"at":"home","seq":1}', '[{"send":"home","token":1}]'), "step", False),
]
for text, op, accepted in VALIDATOR:
    total += 1
    answered += 1
    try:
        check_reply(json.loads(text), 5, op)
        got = True
    except ProtocolError:
        got = False
    if got != accepted:
        fail(f"validator on {text}", f"{'accepted' if got else 'rejected'}, expected {'accepted' if accepted else 'rejected'}")

if json_only:
    print(f"protocol edge cases {total}, replies {answered}, mismatches {bad}")
    sys.exit(1 if bad or total == 0 else 0)

run_raw("raw lines", [], RAW, final_newline=False)
run_raw("--max-request 64", ["--max-request", "64"], LIMITED)
run_raw("--max-request 0", ["--max-request", "0"], UNLIMITED)

# ---- command line: mutants only in test mode, wrong arguments --------------------
no_mut = {k: v for k, v in os.environ.items() if k != "FIGRAM_ALLOW_MUTANTS"}


def cli(args, data=b"", env=None):
    return subprocess.run([engine] + args, input=data, capture_output=True, timeout=default_timeout(), env=env)


def check_cli(what, cond, r):
    global answered, total
    total += 1
    answered += 1
    if not cond:
        fail(what, f"exit code {r.returncode}, stdout {r.stdout[:120]!r}, stderr {r.stderr[:200]!r}")


hello = b'{"id":1,"op":"hello"}\n'
r = cli(["--mutants", doc], env=no_mut)
check_cli("--mutants without FIGRAM_ALLOW_MUTANTS — refused",
          r.returncode != 0 and r.stdout == b"" and b"FIGRAM_ALLOW_MUTANTS" in r.stderr, r)
r = cli(["--mutants", doc], env=dict(no_mut, FIGRAM_ALLOW_MUTANTS="0"))
check_cli("--mutants with FIGRAM_ALLOW_MUTANTS=0 — refused", r.returncode != 0 and r.stdout == b"", r)
r = cli(["--mutants", doc], env=mutant_env())
mutants = r.stdout.decode().split()
check_cli("--mutants with FIGRAM_ALLOW_MUTANTS=1 — the list", r.returncode == 0 and len(mutants) > 0, r)
spec = mutants[0] if mutants else "drop-call:charge"
r = cli(["--mutate", spec, doc], hello, env=no_mut)
check_cli("--mutate without FIGRAM_ALLOW_MUTANTS — refused, no reply", r.returncode != 0 and r.stdout == b"", r)
r = cli(["--mutate", spec, doc], hello, env=mutant_env())
try:
    mresp = json.loads(r.stdout)
except ValueError:
    mresp = None
check_cli("--mutate: hello reports the mutation, a warning on stderr",
          r.returncode == 0 and mresp == {"id": 1, **HELLO, "mutant": spec} and spec.encode() in r.stderr, r)
r = cli(["--mutate", "redirect:999", doc], hello, env=mutant_env())
check_cli("--mutate redirect:999 (no such number in the machine) — refused", r.returncode == 2 and r.stdout == b"", r)
r = cli(["--mutate", "drop-call:nope", doc], hello, env=mutant_env())
check_cli("--mutate drop-call:nope (no such action in the machine) — refused", r.returncode == 2 and r.stdout == b"", r)
r = cli(["--mutate", "bogus", doc], hello, env=mutant_env())
check_cli("--mutate bogus — refused", r.returncode != 0 and r.stdout == b"", r)
for bad_args in (["--max-request", "abc", doc], ["--max-request", "-5", doc], ["--max-request", doc],
                 ["--bogus", doc], []):
    r = cli(bad_args, hello)
    check_cli(f"arguments {bad_args} — refused with exit code 2", r.returncode == 2 and r.stdout == b"", r)
r = cli(["--max-request", "100", doc], hello)
try:
    lresp = json.loads(r.stdout)
except ValueError:
    lresp = None
check_cli("--max-request 100 before the document — works", r.returncode == 0 and lresp == {"id": 1, **HELLO}, r)

print(f"protocol edge cases {total}, replies {answered}, mismatches {bad}")
sys.exit(1 if bad else 0)

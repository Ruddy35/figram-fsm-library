#!/usr/bin/env python3
"""Directed, table-driven edge cases for the decisions of the specification 1-13
(docs/format.md, section 7: 11 background actions, 12 outcomes, 13 arrows without a target)
and the engine protocol (section 8: `pending`, rule 2, the `call` fields, `restart`).

Every expectation below is written BY HAND from the prose spec, and is
checked against BOTH the real engine and the independent oracle (so the
oracle itself is validated against the prose, too).

  python3 directed.py --engine BIN [--group conformance|findings|all] [--mutate M]
                      [--oracle-only] [--expect-red]

group conformance: behaviour the spec states explicitly (must pass).
group findings:    designer-level expectations that the baseline VIOLATES
                   (commit d69bbaf, the code before the audit fixes; audit archive:
                   commit 16c885a): regression tests of the defects found during the
                   audit (H2 timers after a document upgrade, H3 `need`); they pass
                   on the current engine and FAIL on the baseline.
"""

import argparse
import json
import sys

from engine import Engine
from oracle import MAX_INT, Bot, Options, snap_from_json, snap_to_json

C = {"conditions": {"c1": "c1", "c2": "c2", "c3": "c3"}, "calls": {"k1": "k1", "k2": "k2"}}


def doc(bot, **extra):
    d = {"figram": 1}
    d.update(C)
    d["bot"] = bot
    d.update(extra)
    return d


def sec(initial, children, on=None, **ann):
    o = {"initial": initial}
    if on:
        o["on"] = on
    o["children"] = children
    o.update(ann)
    return o


def msg(on=None, timers=None, **ann):
    o = {}
    if on:
        o["on"] = on
    if timers:
        o["timers"] = timers
    o.update(ann)
    return o


def go(to, cond=None, resume=False, do=None, back=False):
    v = {}
    if cond:
        v["if"] = cond
    v["to"] = to
    if resume:
        v["resume"] = True
    if back:
        v["back"] = True
    if do:
        v["do"] = do
    return v


def stay(cond=None, do=None):
    """A variant without a target (docs/format.md, section 7, decision 13)."""
    v = {}
    if cond:
        v["if"] = cond
    if do:
        v["do"] = do
    return v


S = lambda n: {"send": n}
CALL = lambda n: {"call": n}


def CALLT(n, tok=None, detached=False):
    """`{"call": n, "token"?: tok, "detached"?: true}` in this key order (section 8)."""
    a = {"call": n}
    if tok is not None:
        a["token"] = tok
    if detached:
        a["detached"] = True
    return a


W = lambda a, tok: {"call": a, "token": tok}   # a wait of `pending`
START = lambda t, ms, tok: {"start_timer": t, "ms": ms, "token": tok}
CANCEL = lambda t: {"cancel_timer": t}

# ---------------------------------------------------------------- documents
D_NEST_INIT = doc(sec("S1", {"S1": sec("S2", {"S2": sec("m", {"m": msg(timers={"a": 10, "b": 20})})})}))

D_BUBBLE = doc(sec("S", {
    "S": sec("m", {
        "m": msg(on={"btn:x": [go("A", "c1")], "btn:y": [go("A")]}),
        "A": msg(), "B": msg()},
        on={"btn:x": [go("B", "c2")], "btn:y": [go("B", "c1")], "btn:z": [go("B")]}),
    "C": msg()},
    on={"btn:x": [go("C")], "btn:w": [go("C", "c3")]}))

D_ORDER = doc(sec("m", {
    "m": msg(on={"btn:go": [go("n", do=["k1", "k2", "k1"])], "btn:self": [go("m")],
                 "timer:t1": [go("q")]},
             timers={"t1": 5, "t2": 6}),
    "S": sec("n", {"n": msg(timers={"u": 7}), "n2": msg()}),
    "q": msg(on={"btn:back": [go("S", resume=True)]})}))

D_HIST = doc(sec("S", {
    "S": sec("T", {
        "T": sec("x", {"x": msg(on={"btn:n": [go("y")]}), "y": msg(on={"btn:z": [go("z")]})}),
        "z": msg(on={"btn:r": [go("S", resume=True)]}, timers={"tz": 3})},
        on={"cmd:help": [go("h")], "btn:rs": [go("S", resume=True)], "btn:home": [go("S")]}),
    "h": msg(on={"btn:S": [go("S", resume=True)], "btn:T": [go("T", resume=True)],
                 "btn:P": [go("P", resume=True)]}),
    "P": sec("Q", {"Q": sec("q1", {"q1": msg(), "q2": msg()}), "p": msg()}),
    "plain": sec("pl", {"pl": msg()})}))

D_TIMERS = doc(sec("S", {
    "S": sec("a", {
        "a": msg(on={"btn:b": [go("b")]}, timers={"t": 100}),
        "b": msg(on={"timer:t": [go("own")], "btn:a": [go("a")]}, timers={"t": 200}),
        "c": msg(on={"btn:a": [go("a")]}, timers={"v": 1}),
        "own": msg()},
        on={"timer:v": [go("secv")]}),
    "secv": msg(), "rootv": msg(),
    "d": msg(timers={"w": 9})},
    on={"timer:w": [go("rootv")], "btn:d": [go("d")], "btn:c": [go("c")]}))

D_RECOVER = doc(sec("H", {
    "H": sec("i", {"i": msg(timers={"it": 4}), "j": msg()}),
    "o": msg(on={"btn:back": [go("H", resume=True)]})}))

# Recovery with an event whose arrow (of the root section) resumes H: the kept remembered place is used.
D_RECOVER_RES = doc(sec("H", {
    "H": sec("i", {"i": msg(), "j": msg()}),
    "o": msg()},
    on={"btn:r": [go("H", resume=True)]}))

# "Back to the previous section" (decision 10): sections CAT (with the subsection SUB) and HELP;
# menu and x are directly in the root section.
D_BACK = doc(sec("menu", {
    "menu": msg(on={"btn:cat": [go("CAT")], "btn:help": [go("HELP")]}),
    "CAT": sec("c1", {
        "c1": msg(on={"btn:n": [go("c2")]}),
        "c2": msg(on={"btn:sub": [go("SUB")]}),
        "SUB": sec("s1", {"s1": msg(on={"btn:up": [go("c2")]})})}),
    "HELP": sec("h", {"h": msg()}),
    "x": msg()},
    on={"btn:back": [go("menu", back=True)], "btn:backres": [go("CAT", resume=True, back=True)]}))

# Document upgrade (docs/host-contract.md, section 6): v1 -- reminder "remind" on A; v2 -- the designer moved
# that reminder to B.  Section S handles timer:remind for any message inside.
D_UP_V1 = doc(sec("S", {
    "S": sec("A", {
        "A": msg(on={"btn:next": [go("C")]}, timers={"remind": 60000}),
        "B": msg(),
        "C": msg(on={"btn:x": [go("D")]}),
        "D": msg()},
        on={"timer:remind": [go("R", do=["k1"])]}),
    "R": msg()}))
D_UP_V2 = doc(sec("S", {
    "S": sec("A", {
        "A": msg(on={"btn:next": [go("C")]}),
        "B": msg(timers={"remind": 60000}),
        "C": msg(on={"btn:x": [go("D")]}),
        "D": msg()},
        on={"timer:remind": [go("R", do=["k1"])]}),
    "R": msg()}))
# Same but the designer only RENAMED the reminder on A (remind -> nudge);
# the old name stays handled on the message that owns it elsewhere (E).
D_UP_V2_RENAME = doc(sec("S", {
    "S": sec("A", {
        "A": msg(on={"btn:next": [go("C")], "timer:nudge": [go("R")]}, timers={"nudge": 60000}),
        "C": msg(on={"btn:x": [go("D")], "timer:remind": [go("R")]}),
        "D": msg(), "E": msg(timers={"remind": 1})}),
    "R": msg()}))

# ---- 0.3.0: background actions, actions with outcomes, arrows without a target
# (docs/format.md, section 7, decisions 11-13; section 8: `pending`, rule 2, `call` fields)
C3 = {"conditions": {"c1": "c1", "c2": "c2", "c3": "c3"},
      "calls": {"k1": "k1",
                "bg": {"text": "a background action", "detached": True},
                "chk": {"text": "ask the warehouse", "outcomes": ["yes", "no"]},
                "ping": {"text": "ping, every call answered", "outcomes": ["pong"], "every": True},
                "bgo": {"text": "background with outcomes", "outcomes": ["done"], "detached": True},
                "a:b": {"text": "a name with a colon", "outcomes": ["ok"]}}}


def doc3(bot):
    d = {"figram": 1}
    d.update(C3)
    d["bot"] = bot
    return d


D_OUT = doc3(sec("home", {
    "home": msg(on={"btn:go": [go("wait", do=["chk"])], "btn:two": [go("wait", do=["chk", "ping"])],
                    "btn:w2": [go("wait2", do=["chk"])]},
                timers={"idle": 100}),
    "wait": msg(on={"call:chk:yes": [go("yes")], "call:chk:no": [go("no", "c1")],
                    "call:chk:failed": [go("err")], "btn:retry": [stay(do=["chk"])],
                    "btn:home": [go("home")]},
                timers={"tw": 50}),
    "wait2": msg(on={"call:chk:failed": [stay(do=["chk"])], "call:chk:yes": [go("yes")],
                     "call:chk:no": [go("no")]}),
    "yes": msg(), "no": msg(),
    "err": msg(on={"btn:home": [go("home")]})},
    on={"btn:ping": [stay(do=["ping"])], "call:ping:pong": [stay(do=["k1"])],
        "call:ping:failed": [go("err")], "btn:chkhere": [stay(do=["chk"])],
        "btn:bg": [stay(do=["bg", "bgo"])], "btn:ab": [stay(do=["a:b"])],
        "call:a:b:ok": [go("yes")], "call:a:b:failed": [go("err")],
        "call:bgo:done": [stay()], "call:bgo:failed": [stay()]}))

# Arrows without a target: on a message (named and timer events), on a section, on the root;
# section S is remembered (resume arrow from o) and o is outside S ("back").
D_STAY = doc3(sec("S", {
    "S": sec("m", {
        "m": msg(on={"timer:tick": [stay(do=["k1"])], "btn:stay": [go("n", "c1"), stay(do=["k1"])],
                     "btn:leave": [go("n")], "btn:chk": [stay(do=["chk"])],
                     "call:chk:yes": [stay(do=["bg"])], "call:chk:no": [stay()],
                     "call:chk:failed": [stay()]},
                 timers={"tick": 10, "other": 20}),
        "n": msg(on={"call:chk:yes": [go("m")], "call:chk:no": [go("m")], "call:chk:failed": [go("m")]})},
        on={"btn:sec": [stay(do=["k1"])], "btn:shadow": [stay(do=["k1"])]}),
    "o": msg(on={"btn:back": [go("S", resume=True)], "btn:prev": [go("m", back=True)]})},
    on={"btn:root": [stay(do=["k1"])], "btn:shadow": [go("o")], "btn:x": [go("o")]}))

# ---------------------------------------------------------------- cases
# step = (event, token, guards, expect)  expect keys: at, actions, timers,
# resume (exact dict), resume_has (subset), seq, recovered, need (set),
# need_superset_of, error, unchanged (bool)
CASES = []


def case(name, group, d, steps, start=True, snapshot=None, spec=""):
    CASES.append({"name": name, "group": group, "doc": d, "steps": steps,
                  "start": start, "snapshot": snapshot, "spec": spec})


case("start_nested_initials", "conformance", D_NEST_INIT, [], spec="section whose initial is a section; entry order")
CASES[-1]["start_expect"] = {"at": "m", "seq": 1, "timers": {"a": 1, "b": 1},
                             "actions": [START("a", 10, 1), START("b", 20, 1), S("m")]}

case("bubble_all_false_to_root", "conformance", D_BUBBLE,
     [("btn:x", None, {"c1": False, "c2": False}, {"at": "C", "actions": [S("C")], "seq": 2})], spec="decision 1")
case("bubble_false_then_section_true", "conformance", D_BUBBLE,
     [("btn:x", None, {"c1": False, "c2": True}, {"at": "B", "actions": [S("B")]})], spec="decision 1")
case("message_true_wins", "conformance", D_BUBBLE,
     [("btn:x", None, {"c1": True, "c2": False}, {"at": "A"})], spec="decision 1")
case("need_lists_chain_guards", "conformance", D_BUBBLE,
     [("btn:x", None, None, {"need_superset_of": {"c1", "c2"}})], spec="format.md §8, need")
case("section_arrow_message_lacks", "conformance", D_BUBBLE,
     [("btn:z", None, {}, {"at": "B", "actions": [S("B")]})], spec="section arrow works on any message inside")
case("root_guarded_false_ignored", "conformance", D_BUBBLE,
     [("btn:w", None, {"c3": False}, {"unchanged": True})], spec="decision 6")
case("unknown_event_ignored", "conformance", D_BUBBLE,
     [("btn:nothing", None, {}, {"unchanged": True})], spec="decision 6, seq not bumped")
case("unconditional_message_shadows_section", "conformance", D_BUBBLE,
     [("btn:y", None, {"c1": True}, {"at": "A"})], spec="decision 1")
case("irrelevant_guard_ignored", "conformance", D_BUBBLE,
     [("btn:z", None, {"zzz": True, "c1": True}, {"at": "B"})], spec="guards outside the chain")
case("partial_guards_need_rest", "conformance", D_BUBBLE,
     [("btn:x", None, {"c1": False}, {"need_superset_of": {"c2"}, "need_excludes": {"c1"}})], spec="format.md §8, need")

case("action_order_exit_arrow_entry", "conformance", D_ORDER,
     [("btn:go", None, {}, {"at": "n", "seq": 2, "timers": {"u": 2},
                            "actions": [CANCEL("t1"), CANCEL("t2"), CALL("k1"), CALL("k2"), CALL("k1"),
                                        START("u", 7, 2), S("n")]})], spec="decision 2")
case("self_loop_restarts_timers", "conformance", D_ORDER,
     [("btn:self", None, {}, {"at": "m", "seq": 2, "timers": {"t1": 2, "t2": 2},
                              "actions": [CANCEL("t1"), CANCEL("t2"), START("t1", 5, 2), START("t2", 6, 2), S("m")]}),
      ("timer:t1", 1, {}, {"unchanged": True}),
      ("timer:t1", 2, {}, {"at": "q", "timers": {}, "actions": [CANCEL("t1"), CANCEL("t2"), S("q")]})],
     spec="decisions 3, 7")
case("resume_no_memory_goes_initial", "conformance", D_ORDER,
     [("timer:t1", 1, {}, {"at": "q"}), ("btn:back", None, {}, {"at": "n", "resume": {"S": "n"}})],
     spec="decision 5 / resume without memory")

case("remember_on_entry_nested", "conformance", D_HIST,
     [("btn:n", None, {}, {"at": "y", "resume": {"S": "y", "T": "y"}}),
      ("cmd:help", None, {}, {"at": "h", "resume": {"S": "y", "T": "y"}}),
      ("btn:S", None, {}, {"at": "y", "actions": [S("y")]})],
     spec="decision 4, deep history")
case("nested_outer_and_inner_memory", "conformance", D_HIST,
     [("btn:n", None, {}, {"at": "y"}), ("btn:z", None, {}, {"at": "z", "resume": {"S": "z", "T": "y"}}),
      ("cmd:help", None, {}, {"at": "h"}), ("btn:T", None, {}, {"at": "y"}),
      ("cmd:help", None, {}, {"at": "h"}), ("btn:S", None, {}, {"at": "y"})],
     spec="decision 4: entering y re-remembers S")
case("resume_from_inside_is_current", "conformance", D_HIST,
     [("btn:n", None, {}, {"at": "y"}), ("btn:z", None, {}, {"at": "z", "seq": 3, "timers": {"tz": 3}}),
      ("btn:r", None, {}, {"at": "z", "seq": 4, "timers": {"tz": 4},
                           "actions": [CANCEL("tz"), START("tz", 3, 4), S("z")]}),
      ("btn:rs", None, {}, {"at": "z"})],
     spec="Chart doc: from inside the section resume = current message")
case("resume_section_initial_is_section", "conformance", D_HIST,
     [("cmd:help", None, {}, {"at": "h"}), ("btn:P", None, {}, {"at": "q1", "resume_has": {"P": "q1"}})],
     spec="resume, no memory, initial chain P->Q->q1")
case("arrow_to_section_enters_initial", "conformance", D_HIST,
     [("btn:n", None, {}, {"at": "y"}), ("btn:home", None, {}, {"at": "x"})],
     spec="arrow into section leads to its initial message (not history)")
case("resume_memory_deleted", "conformance", D_HIST,
     [("btn:S", None, {}, {"at": "x"})], start=False,
     snapshot={"at": "h", "resume": {"S": "gone"}, "seq": 5}, spec="decision 5")
case("resume_memory_is_section_now", "conformance", D_HIST,
     [("btn:S", None, {}, {"at": "x"})], start=False,
     snapshot={"at": "h", "resume": {"S": "T"}, "seq": 5}, spec="decision 5 (message became section)")
case("resume_memory_moved_outside", "conformance", D_HIST,
     [("btn:S", None, {}, {"at": "x"})], start=False,
     snapshot={"at": "h", "resume": {"S": "pl"}, "seq": 5},
     spec="AMBIGUITY A1: remembered message exists but outside S -> Lean: initial")
case("resume_memory_inner_valid", "conformance", D_HIST,
     [("btn:S", None, {}, {"at": "z"})], start=False,
     snapshot={"at": "h", "resume": {"S": "z", "T": "x"}, "seq": 5}, spec="decision 4/5")

case("recover_deleted_current", "conformance", D_RECOVER,
     [("btn:anything", None, {}, {"recovered": True, "at": "i", "seq": 10,
                                  "actions": [CANCEL("old"), CANCEL("it"), START("it", 4, 10), S("i")],
                                  "timers": {"it": 10}, "resume": {"H": "i", "X": "y"}})],
     start=False, snapshot={"at": "gone", "resume": {"H": "j", "X": "y"}, "timers": {"old": 3, "it": 2}, "seq": 9},
     spec="decision 8; AMBIGUITY A4: entry of initial re-remembers H (overwrites 'kept' place)")
case("recover_at_is_section", "conformance", D_RECOVER,
     [("timer:it", 2, {}, {"recovered": True, "at": "i"})], start=False,
     snapshot={"at": "H", "timers": {"it": 2}, "seq": 2}, spec="snapshot points at a section, not a message")
case("recover_resume_uses_kept_place", "conformance", D_RECOVER_RES,
     [("btn:r", None, {}, {"recovered": True, "at": "j", "seq": 5, "actions": [S("j")], "resume": {"H": "j"}})],
     start=False, snapshot={"at": "gone", "resume": {"H": "j"}, "seq": 4},
     spec="decisions 5, 8, 9: the event of a recovered step resumes H at the kept place")
case("recover_ignores_token", "conformance", D_RECOVER,
     [("timer:zz", 77, {}, {"recovered": True, "at": "i"})], start=False,
     snapshot={"at": "gone", "seq": 2}, spec="recovery happens before token check")

case("stale_forged_tokens", "conformance", D_TIMERS,
     [("btn:b", None, {}, {"at": "b", "timers": {"t": 2}, "actions": [CANCEL("t"), START("t", 200, 2), S("b")]}),
      ("timer:t", 1, {}, {"unchanged": True}),
      ("timer:t", MAX_INT, {}, {"unchanged": True}),
      ("timer:nope", 2, {}, {"unchanged": True}),
      ("timer:t", 2, {}, {"at": "own", "actions": [CANCEL("t"), S("own")]})],
     spec="decision 7, shared timer id on two messages")
case("valid_timer_no_handler_ignored", "conformance", D_TIMERS,
     [("timer:t", 1, {}, {"unchanged": True})], spec="decision 6 for timers")
case("section_level_timer_handler", "conformance", D_TIMERS,
     [("btn:c", None, {}, {"at": "c", "timers": {"v": 2}}),
      ("timer:v", 2, {}, {"at": "secv", "actions": [CANCEL("v"), S("secv")], "timers": {}})],
     spec="section arrow on timer:<id>")
case("root_level_timer_handler", "conformance", D_TIMERS,
     [("btn:d", None, {}, {"at": "d"}), ("timer:w", 2, {}, {"at": "rootv", "timers": {}})],
     spec="root arrow on timer:<id>")

case("seq_overflow_transition", "conformance", D_BUBBLE,
     [("btn:z", None, {}, {"error": "seq_overflow"})], start=False,
     snapshot={"at": "m", "seq": MAX_INT}, spec="format.md §8, seq_overflow")
case("seq_max_ignored_ok", "conformance", D_BUBBLE,
     [("btn:none", None, {}, {"unchanged": True})], start=False,
     snapshot={"at": "m", "seq": MAX_INT}, spec="ignored event does not bump seq")
case("restart_clears_places", "conformance", D_RECOVER,
     [("@restart", None, None, {"at": "i", "seq": 8, "resume": {"H": "i"},
                                "actions": [CANCEL("x"), START("it", 4, 8), S("i")]})], start=False,
     snapshot={"at": "o", "resume": {"H": "j"}, "timers": {"x": 1}, "seq": 7}, spec="format.md §8, restart")

case("back_cross_records_inside_keeps", "conformance", D_BACK,
     [("btn:cat", None, {}, {"at": "c1", "back": "menu"}),
      ("btn:n", None, {}, {"at": "c2", "back": "menu"}),
      ("btn:back", None, {}, {"at": "menu", "back": "c2", "actions": [S("menu")]}),
      ("btn:back", None, {}, {"at": "c2", "back": "menu"})],
     spec="decision 10: crossing remembers the message left, inside a section it stays, back twice returns")
case("back_subsection", "conformance", D_BACK,
     [("btn:cat", None, {}, {"at": "c1"}), ("btn:n", None, {}, {"at": "c2"}),
      ("btn:sub", None, {}, {"at": "s1", "back": "c2"}),
      ("btn:up", None, {}, {"at": "c2", "back": "s1"}),
      ("btn:back", None, {}, {"at": "s1", "back": "c2"})],
     spec="decision 10: entering and leaving a subsection are crossings")
case("back_new_user_fallback", "conformance", D_BACK,
     [("btn:back", None, {}, {"at": "menu", "back": None, "actions": [S("menu")]})],
     spec="decision 10: no place yet -> `to`; menu to menu crosses nothing")
case("back_root_messages_cross_nothing", "conformance", D_BACK,
     [("btn:back", None, {}, {"at": "menu", "back": None})], start=False,
     snapshot={"at": "x", "seq": 3}, spec="decision 10: x and menu are both directly in the root")
case("back_place_gone", "conformance", D_BACK,
     [("btn:back", None, {}, {"at": "menu", "back": "h"})], start=False,
     snapshot={"at": "h", "back": "gone", "seq": 3}, spec="decision 10: a place no longer in the document -> `to`")
case("back_resume_fallback", "conformance", D_BACK,
     [("btn:backres", None, {}, {"at": "c2", "back": "h", "resume": {"CAT": "c2"}})], start=False,
     snapshot={"at": "h", "resume": {"CAT": "c2"}, "seq": 3}, spec="decision 10 with resume: no place -> resume CAT")
case("back_restart_clears", "conformance", D_BACK,
     [("@restart", None, None, {"at": "menu", "back": None, "seq": 3})], start=False,
     snapshot={"at": "c1", "back": "menu", "seq": 2}, spec="format.md §8, restart clears the place")
case("back_recovery_keeps", "conformance", D_BACK,
     [("btn:back", None, {}, {"at": "c2", "back": "c2", "recovered": True})], start=False,
     snapshot={"at": "gone", "back": "c2", "seq": 4}, spec="decisions 8, 10: recovery keeps the place")

# ------------- 0.3.0: outcomes (decision 12), background actions (11), no target (13)
OUT_START = {"at": "home", "timers": {"idle": 1}, "seq": 1}
case("outcome_accepted_only_with_token", "conformance", D_OUT,
     [("btn:go", None, {}, {"snapshot": {"at": "wait", "timers": {"tw": 2}, "pending": [W("chk", 2)], "seq": 2},
                            "actions": [CANCEL("idle"), CALLT("chk", 2), START("tw", 50, 2), S("wait")]}),
      ("call:chk:yes", 1, {}, {"unchanged": True}),
      ("call:chk:yes", 3, {}, {"unchanged": True}),
      ("call:chk:yes", 2, {}, {"snapshot": {"at": "yes", "seq": 3}, "actions": [CANCEL("tw"), S("yes")]}),
      ("call:chk:yes", 2, {}, {"unchanged": True})],
     spec="decision 12, rule 2: an outcome is accepted only with the token of a wait (= the seq of the step "
          "that called the action); accepting removes the wait, a repeated delivery is ignored")
case("outcome_wrong_action_same_token", "conformance", D_OUT,
     [("call:ping:pong", 2, {}, {"unchanged": True}),
      ("call:chk:maybe", 3, {}, {"unchanged": True})], start=False,
     snapshot={"at": "wait", "timers": {"tw": 2}, "pending": [W("chk", 2)], "seq": 2},
     spec="rule 2: the wait must be for THAT action with that token")
case("outcome_without_arrow_removes_wait", "conformance", D_OUT,
     [("btn:go", None, {}, {"at": "wait"}),
      ("btn:home", None, {}, {"snapshot": {"at": "home", "timers": {"idle": 3}, "pending": [W("chk", 2)], "seq": 3},
                              "actions": [CANCEL("tw"), START("idle", 100, 3), S("home")]}),
      ("call:chk:yes", 2, {}, {"snapshot": {"at": "home", "timers": {"idle": 3}, "seq": 3}, "actions": []}),
      ("call:chk:yes", 2, {}, {"unchanged": True})],
     spec="decision 12: moves keep the waits; an accepted outcome without an arrow is ignored, but its wait is "
          "gone (the reply carries the snapshot without it, seq unchanged)")
case("outcome_latest_replaces", "conformance", D_OUT,
     [("btn:go", None, {}, {"pending": [W("chk", 2)]}),
      ("btn:retry", None, {}, {"snapshot": {"at": "wait", "timers": {"tw": 2}, "pending": [W("chk", 3)], "seq": 3},
                               "actions": [CALLT("chk", 3)]}),
      ("call:chk:yes", 2, {}, {"unchanged": True}),
      ("call:chk:yes", 3, {}, {"at": "yes", "pending": [], "seq": 4})],
     spec="decision 12: a new call of the same action replaces its wait, the outcome of the earlier call is ignored")
case("outcome_latest_replaces_several", "conformance", D_OUT,
     [("btn:retry", None, {}, {"pending": [W("chk", 6), W("ping", 4)], "actions": [CALLT("chk", 6)]})], start=False,
     snapshot={"at": "wait", "timers": {"tw": 2}, "pending": [W("chk", 5), W("ping", 4), W("chk", 3)], "seq": 5},
     spec="decision 12 (reading A10): only the latest call is answered: every older wait of a non-`every` action "
          "(a snapshot written while it had `every`) is replaced")
case("outcome_every_keeps_each", "conformance", D_OUT,
     [("btn:ping", None, {}, {"snapshot": {"at": "home", "timers": {"idle": 1}, "pending": [W("ping", 2)], "seq": 2},
                              "actions": [CALLT("ping", 2)]}),
      ("btn:ping", None, {}, {"snapshot": {"at": "home", "timers": {"idle": 1}, "pending": [W("ping", 3), W("ping", 2)],
                                           "seq": 3}, "actions": [CALLT("ping", 3)]}),
      ("call:ping:pong", 2, {}, {"snapshot": {"at": "home", "timers": {"idle": 1}, "pending": [W("ping", 3)], "seq": 4},
                                 "actions": [CALLT("k1")]}),
      ("call:ping:pong", 2, {}, {"unchanged": True}),
      ("call:ping:pong", 3, {}, {"snapshot": {"at": "home", "timers": {"idle": 1}, "seq": 5}, "actions": [CALLT("k1")]})],
     spec="decision 12: with `every` each call has its own wait (the newest first in `pending`) and each outcome "
          "is accepted once")
case("outcome_on_root_anywhere", "conformance", D_OUT,
     [("btn:ping", None, {}, {"pending": [W("ping", 2)]}),
      ("btn:go", None, {}, {"snapshot": {"at": "wait", "timers": {"tw": 3}, "pending": [W("chk", 3), W("ping", 2)],
                                         "seq": 3}}),
      ("call:ping:failed", 2, {}, {"snapshot": {"at": "err", "pending": [W("chk", 3)], "seq": 4},
                                   "actions": [CANCEL("tw"), S("err")]}),
      ("call:chk:yes", 3, {}, {"snapshot": {"at": "err", "seq": 4}, "actions": []})],
     spec="decision 12: an arrow on the root handles the outcome wherever the user is; an arrow on the waiting "
          "message handles it only there (the user left `wait`: `call:chk:yes` is ignored on `err`)")
case("outcome_two_waits_one_step", "conformance", D_OUT,
     [("btn:two", None, {}, {"snapshot": {"at": "wait", "timers": {"tw": 2}, "pending": [W("ping", 2), W("chk", 2)],
                                          "seq": 2},
                             "actions": [CANCEL("idle"), CALLT("chk", 2), CALLT("ping", 2), START("tw", 50, 2),
                                         S("wait")]})],
     spec="decisions 2, 12 (reading A9): both calls get the step's token; `pending` lists the newest first: "
          "`ping` is performed after `chk`")
case("outcome_need_then_bubble", "conformance", D_OUT,
     [("call:chk:no", 2, None, {"need": {"c1"}}),
      ("call:chk:no", 2, {"c1": False}, {"snapshot": {"at": "wait", "timers": {"tw": 2}, "seq": 2}, "actions": []})],
     start=False, snapshot={"at": "wait", "timers": {"tw": 2}, "pending": [W("chk", 2)], "seq": 2},
     spec="decision 12, rules 2-4: an accepted outcome is an event like any other: `need`; all conditions "
          "false and no arrow above -> ignored, the wait removed")
case("outcome_need_true_moves", "conformance", D_OUT,
     [("call:chk:no", 2, {"c1": True}, {"snapshot": {"at": "no", "seq": 3}, "actions": [CANCEL("tw"), S("no")]})],
     start=False, snapshot={"at": "wait", "timers": {"tw": 2}, "pending": [W("chk", 2)], "seq": 2},
     spec="decision 12: the outcome arrow on the waiting message")
case("outcome_not_accepted_needs_nothing", "conformance", D_OUT,
     [("call:chk:no", 2, None, {"unchanged": True})], start=False,
     snapshot={"at": "wait", "timers": {"tw": 2}, "pending": [W("chk", 1)], "seq": 2},
     spec="rule 2: an outcome without its wait is ignored before any condition is asked")
case("outcome_of_removed_action_accepted", "conformance", D_OUT,
     [("call:gone:ok", 4, {}, {"snapshot": {"at": "home", "pending": [W("k1", 2), W("chk", 3)], "seq": 5},
                               "actions": []}),
      ("call:k1:failed", 2, {}, {"snapshot": {"at": "home", "pending": [W("chk", 3)], "seq": 5}, "actions": []})],
     start=False, snapshot={"at": "home", "pending": [W("gone", 4), W("k1", 2), W("chk", 3)], "seq": 5},
     spec="section 8: a wait of an action that is no longer in the document, or has no outcomes, is kept and "
          "still accepts its outcome")
case("outcome_undeclared_name_accepted", "conformance", D_OUT,
     [("call:chk:maybe", 3, {}, {"snapshot": {"at": "home", "seq": 5}, "actions": []})],
     start=False, snapshot={"at": "home", "pending": [W("chk", 3)], "seq": 5},
     spec="rule 2 (reading A12): acceptance depends on the wait (action, token) only; no arrow can exist for an "
          "undeclared outcome (V5), so it is ignored and the wait is gone")
case("outcome_duplicate_wait_reading", "conformance", D_OUT,
     [("call:chk:yes", 3, {}, {"snapshot": {"at": "home", "pending": [W("ping", 4)], "seq": 5}, "actions": []})],
     start=False, snapshot={"at": "home", "pending": [W("chk", 3), W("ping", 4), W("chk", 3)], "seq": 5},
     spec="AMBIGUITY A11 (only a forged snapshot holds the same wait twice): the engine removes every copy; "
          "the literal 'each wait accepts one outcome' would keep one (--oracle-opt dup_accept=one)")
case("outcome_same_wait_reading", "conformance", D_OUT,
     [("btn:ping", None, {}, {"snapshot": {"at": "home", "timers": {"idle": 1},
                                           "pending": [W("ping", 2), W("x", 1), W("y", 1)], "seq": 2},
                              "actions": [CALLT("ping", 2)]})],
     start=False, snapshot={"at": "home", "timers": {"idle": 1}, "pending": [W("x", 1), W("ping", 2), W("y", 1)],
                            "seq": 1},
     spec="AMBIGUITY A15 (only a forged snapshot holds a wait with a token not below seq): the new wait of an "
          "`every` action replaces an identical one (--oracle-opt same_wait=keep would keep both)")
case("outcome_retry_on_failed", "conformance", D_OUT,
     [("btn:w2", None, {}, {"snapshot": {"at": "wait2", "pending": [W("chk", 2)], "seq": 2}}),
      ("call:chk:failed", 2, {}, {"snapshot": {"at": "wait2", "pending": [W("chk", 3)], "seq": 3},
                                  "actions": [CALLT("chk", 3)]}),
      ("call:chk:yes", 3, {}, {"at": "yes", "pending": []})],
     spec="decisions 12, 13: an outcome arrow without a target calls the action again: a new wait with the new token")
case("outcome_colon_in_action_name", "conformance", D_OUT,
     [("btn:ab", None, {}, {"snapshot": {"at": "home", "timers": {"idle": 1}, "pending": [W("a:b", 2)], "seq": 2},
                            "actions": [CALLT("a:b", 2)]}),
      ("call:a:b:ok", 2, {}, {"snapshot": {"at": "yes", "seq": 3}, "actions": [CANCEL("idle"), S("yes")]})],
     spec="section 4: the event is split at its last `:`, the action name may contain `:`")
case("background_and_token_in_calls", "conformance", D_OUT,
     [("btn:bg", None, {}, {"snapshot": {"at": "home", "timers": {"idle": 1}, "pending": [W("bgo", 2)], "seq": 2},
                            "actions": [CALLT("bg", detached=True), CALLT("bgo", 2, detached=True)]}),
      ("call:bgo:done", 2, {}, {"snapshot": {"at": "home", "timers": {"idle": 1}, "seq": 3}, "actions": []}),
      ("btn:go", None, {}, {"actions": [CANCEL("idle"), CALLT("chk", 4), START("tw", 50, 4), S("wait")]})],
     spec="decision 11, section 8: `detached: true` on a background action; `token` (the new seq) exactly "
          "when the action has outcomes; a variant without a target and without actions still makes a "
          "transition (seq + 1)")
case("outcome_accepted_at_max_seq_no_transition", "conformance", D_OUT,
     [("call:chk:yes", 3, {}, {"snapshot": {"at": "home", "seq": MAX_INT}, "actions": []})], start=False,
     snapshot={"at": "home", "pending": [W("chk", 3)], "seq": MAX_INT},
     spec="rule 5: an accepted outcome without an arrow is no transition, so no seq_overflow")
case("outcome_recovery_accepted", "conformance", D_OUT,
     [("call:ping:failed", 4, {}, {"recovered": True, "snapshot": {"at": "err", "pending": [W("chk", 3)], "seq": 7},
                                   "actions": [CANCEL("tw"), S("err")]})], start=False,
     snapshot={"at": "gone", "timers": {"tw": 4}, "pending": [W("ping", 4), W("chk", 3)], "seq": 6},
     spec="decisions 8, 9, 12: recovery keeps the waits; an accepted outcome is matched as at the initial message "
          "(the root arrow leads to err), its wait removed")
case("outcome_recovery_stale", "conformance", D_OUT,
     [("call:ping:failed", 5, {}, {"recovered": True,
                                   "snapshot": {"at": "home", "timers": {"idle": 7},
                                                "pending": [W("ping", 4), W("chk", 3)], "seq": 7},
                                   "actions": [CANCEL("tw"), START("idle", 100, 7), S("home")]})], start=False,
     snapshot={"at": "gone", "timers": {"tw": 4}, "pending": [W("ping", 4), W("chk", 3)], "seq": 6},
     spec="decision 12: an outcome that is not accepted chooses no variant and gives the plain start over "
          "(waits kept)")
case("outcome_recovery_stay_leads_to_initial", "conformance", D_OUT,
     [("call:ping:pong", 4, {}, {"recovered": True,
                                 "snapshot": {"at": "home", "timers": {"idle": 7}, "pending": [W("chk", 3)], "seq": 7},
                                 "actions": [CANCEL("tw"), CALLT("k1"), START("idle", 100, 7), S("home")]})],
     start=False, snapshot={"at": "gone", "timers": {"tw": 4}, "pending": [W("ping", 4), W("chk", 3)], "seq": 6},
     spec="decisions 8, 13: in recovery a variant without a target leads to the initial message")
case("outcome_recovery_accepted_unmatched", "conformance", D_OUT,
     [("call:chk:yes", 3, {}, {"recovered": True,
                               "snapshot": {"at": "home", "timers": {"idle": 7}, "pending": [W("ping", 4)], "seq": 7},
                               "actions": [CANCEL("tw"), START("idle", 100, 7), S("home")]})],
     start=False, snapshot={"at": "gone", "timers": {"tw": 4}, "pending": [W("ping", 4), W("chk", 3)], "seq": 6},
     spec="decision 12, rule 2 (reading A14): accepted (its wait removed), no arrow at the initial message: the "
          "plain start over")
case("outcome_start_rejected", "conformance", D_OUT,
     [("@start=call:chk:yes", None, None, {"code": "bad_request"}),
      ("@start=call:ping:pong", 5, None, {"code": "bad_request"})], start=False, snapshot={"at": "home"},
     spec="decision 12, section 8: a new user has no waits, `start` rejects an outcome event")
case("outcome_start_event_calls", "conformance", D_OUT,
     [("@start=btn:go", 41, None, {"snapshot": {"at": "wait", "timers": {"tw": 42}, "pending": [W("chk", 42)], "seq": 42},
                                   "actions": [CALLT("chk", 42), START("tw", 50, 42), S("wait")]})],
     start=False, snapshot={"at": "home"},
     spec="decision 9, 12: `start` with seq 41 and an event: the call gets the token 42 and a wait")
case("outcome_start_event_stay", "conformance", D_OUT,
     [("@start=btn:chkhere", None, None, {"snapshot": {"at": "home", "timers": {"idle": 1}, "pending": [W("chk", 1)],
                                                       "seq": 1},
                                          "actions": [CALLT("chk", 1), START("idle", 100, 1), S("home")]})],
     start=False, snapshot={"at": "home"},
     spec="decisions 9, 13: at `start` a variant without a target leads to the initial message, its actions in front")
case("outcome_restart_clears", "conformance", D_OUT,
     [("@restart", None, None, {"snapshot": {"at": "home", "timers": {"idle": 5}, "seq": 5},
                                "actions": [CANCEL("tw"), START("idle", 100, 5), S("home")]}),
      ("call:chk:yes", 2, {}, {"unchanged": True})], start=False,
     snapshot={"at": "wait", "back": "err", "timers": {"tw": 2}, "pending": [W("chk", 2), W("ping", 1)], "seq": 4},
     spec="section 8, host-contract.md section 4: `restart` clears the waits (outcomes of earlier actions are then "
          "ignored)")

STAY_SNAP = {"at": "m", "back": "o", "resume": {"S": "m"}, "timers": {"tick": 1, "other": 1}, "seq": 5}
case("stay_start_remembers", "conformance", D_STAY, [], spec="entry of m remembers S")
CASES[-1]["start_expect"] = {"snapshot": {"at": "m", "resume": {"S": "m"}, "timers": {"tick": 1, "other": 1}, "seq": 1},
                             "actions": [START("tick", 10, 1), START("other", 20, 1), S("m")]}
case("stay_keeps_timers_places_back", "conformance", D_STAY,
     [("btn:stay", None, {"c1": False}, {"snapshot": dict(STAY_SNAP, seq=6), "actions": [CALLT("k1")]})],
     start=False, snapshot=STAY_SNAP,
     spec="decision 13: no exit, no entry (no cancel/start/send), timers keep their tokens, places and the place "
          "to go back to unchanged, seq + 1")
case("stay_consumes_fired_timer", "conformance", D_STAY,
     [("timer:tick", 1, {}, {"snapshot": dict(STAY_SNAP, seq=6, timers={"other": 1}), "actions": [CALLT("k1")]}),
      ("timer:tick", 1, {}, {"unchanged": True}),
      ("btn:leave", None, {}, {"snapshot": {"at": "n", "back": "o", "resume": {"S": "n"}, "seq": 7},
                               "actions": [CANCEL("tick"), CANCEL("other"), S("n")]})],
     start=False, snapshot=STAY_SNAP,
     spec="decision 13: a timer firing handled by an arrow without a target is consumed (removed from `timers`, "
          "a redelivery is ignored); leaving cancels the timers of the message by the document (decision 2, "
          "reading A8: also the consumed one)")
case("stay_after_stay_timer_still_valid", "conformance", D_STAY,
     [("btn:root", None, {}, {"snapshot": dict(STAY_SNAP, seq=6), "actions": [CALLT("k1")]}),
      ("timer:tick", 1, {}, {"snapshot": dict(STAY_SNAP, seq=7, timers={"other": 1}), "actions": [CALLT("k1")]})],
     start=False, snapshot=STAY_SNAP,
     spec="decision 13 + 7: an arrow without a target raises seq but leaves the timers (and their tokens) in place: "
          "the old token is still the current one (host-contract.md section 3)")
case("stay_on_section_arrow", "conformance", D_STAY,
     [("btn:sec", None, {}, {"snapshot": dict(STAY_SNAP, seq=6), "actions": [CALLT("k1")]})],
     start=False, snapshot=STAY_SNAP, spec="decision 13 on an arrow of the section around the message")
case("stay_stops_bubbling", "conformance", D_STAY,
     [("btn:shadow", None, {}, {"snapshot": dict(STAY_SNAP, seq=6), "actions": [CALLT("k1")]})],
     start=False, snapshot=STAY_SNAP,
     spec="decision 13: a variant without a target is chosen like any variant, the root's `btn:shadow` -> o is "
          "not reached")
case("stay_on_root_arrow", "conformance", D_STAY,
     [("btn:root", None, {}, {"snapshot": dict(STAY_SNAP, seq=6), "actions": [CALLT("k1")]})],
     start=False, snapshot=STAY_SNAP, spec="decision 13 on the root section")
case("stay_seq_overflow", "conformance", D_STAY,
     [("btn:root", None, {}, {"error": "seq_overflow"})], start=False, snapshot=dict(STAY_SNAP, seq=MAX_INT),
     spec="section 8 rule 5: seq grows by 1 with an arrow without a target too")
case("stay_outcome_waits_on_message", "conformance", D_STAY,
     [("btn:chk", None, {}, {"snapshot": dict(STAY_SNAP, seq=6, pending=[W("chk", 6)]), "actions": [CALLT("chk", 6)]}),
      ("call:chk:yes", 6, {}, {"snapshot": dict(STAY_SNAP, seq=7), "actions": [CALLT("bg", detached=True)]})],
     start=False, snapshot=STAY_SNAP,
     spec="decisions 11-13: a call with outcomes from an arrow without a target; the outcome handled on the same "
          "message without a target")
case("stay_recovery_named_event", "conformance", D_STAY,
     [("btn:root", None, {}, {"recovered": True,
                              "snapshot": {"at": "m", "back": "o", "resume": {"S": "m"},
                                           "timers": {"tick": 10, "other": 10}, "seq": 10},
                              "actions": [CANCEL("zz"), CALLT("k1"), START("tick", 10, 10), START("other", 20, 10),
                                          S("m")]})],
     start=False, snapshot={"at": "gone", "back": "o", "resume": {"S": "n"}, "timers": {"zz": 3}, "seq": 9},
     spec="decisions 8, 13: in recovery a variant without a target leads to the initial message (its entry "
          "remembers S, the place to go back to is kept)")
case("stay_start_event", "conformance", D_STAY,
     [("@start=btn:root", None, None, {"snapshot": {"at": "m", "resume": {"S": "m"}, "timers": {"tick": 1, "other": 1},
                                                    "seq": 1},
                                       "actions": [CALLT("k1"), START("tick", 10, 1), START("other", 20, 1), S("m")]})],
     start=False, snapshot={"at": "m"},
     spec="decisions 9, 13: `start` with an event whose variant has no target: the first entry with the "
          "variant's actions in front")
case("stay_back_resume_unchanged_then_back", "conformance", D_STAY,
     [("btn:x", None, {}, {"snapshot": {"at": "o", "back": "m", "resume": {"S": "m"}, "seq": 2}}),
      ("btn:prev", None, {}, {"snapshot": {"at": "m", "back": "o", "resume": {"S": "m"}, "timers": {"tick": 3, "other": 3},
                                           "seq": 3}})],
     spec="decision 10 with the root arrow btn:x and back")

# ------------- findings: designer-level expectations the baseline violates
case("H2_upgrade_moved_timer_fires_on_other_message", "findings", D_UP_V2,
     [("btn:next", None, {}, {"at": "C", "timers_excludes": {"remind"}}),
      ("btn:x", None, {}, {"at": "D"}),
      ("timer:remind", 1, {}, {"unchanged": True})],
     start=False, snapshot={"at": "A", "timers": {"remind": 1}, "seq": 1},
     spec="host-contract.md §6 + decision 7: v1 snapshot on A with reminder 'remind'; v2 moved the reminder to B. "
          "Leaving A must cancel it (or its fire must be ignored); baseline keeps it forever and the "
          "section handler fires on D, calling k1")
case("H2_upgrade_renamed_timer_unreachable_arrow_fires", "findings", D_UP_V2_RENAME,
     [("btn:next", None, {}, {"at": "C", "timers_excludes": {"remind"}}),
      ("timer:remind", 1, {}, {"unchanged": True})],
     start=False, snapshot={"at": "A", "timers": {"remind": 1}, "seq": 1},
     spec="v2 renamed A's reminder remind->nudge; arrow timer:remind on C (C owns no timer) is "
          "unreachable by design, yet fires on the stale v1 token")
case("H3_need_after_unconditional", "findings", D_BUBBLE,
     [("btn:y", None, None, {"at": "A", "no_need": True})],
     spec="message has an unconditional variant; the section's guarded variant can never win, yet "
          "engine asks the host to evaluate c1")
case("H3b_need_known_true_decides", "findings", D_BUBBLE,
     [("btn:x", None, {"c1": True}, {"at": "A", "no_need": True})],
     spec="the first variant's guard c1 is supplied and true: it wins whatever c2 is, yet the engine "
          "asks the host to evaluate c2")
case("H3c_need_prefix", "findings", D_BUBBLE,
     [("btn:x", None, {}, {"need": {"c1", "c2"}}), ("btn:x", None, {"c1": False}, {"need": {"c2"}})],
     spec="need lists exactly the guards before the first unconditional variant that are not known")


SNAP_FIELDS = ["v", "at", "back", "resume", "timers", "pending", "seq"]


def canon(snap, order=False):
    """JSON text of a snapshot with sorted `resume`/`timers` keys; with order=True the fields
    are first put in the order of docs/format.md, section 8 (for expectations written with dict())."""
    d = dict(snap)
    if order:
        d = {k: d[k] for k in SNAP_FIELDS if k in d}
    for k in ("resume", "timers"):
        if k in d:
            d[k] = dict(sorted(d[k].items()))
    return json.dumps(d)


def check(exp, before, rep, oracle_res, where):
    errs = []
    if "need" in exp:
        if set(rep.get("need", [])) != exp["need"] or "snapshot" in rep:
            errs.append(f"{where}: expected need {sorted(exp['need'])}, got {rep}")
        return errs
    if "need_superset_of" in exp or "need_excludes" in exp:
        if "need" not in rep:
            return [f"{where}: expected need, got {rep}"]
        ns = set(rep["need"])
        if not exp.get("need_superset_of", set()) <= ns:
            errs.append(f"{where}: need {ns} lacks {exp['need_superset_of']}")
        if ns & exp.get("need_excludes", set()):
            errs.append(f"{where}: need {ns} contains provided {exp['need_excludes']}")
        return errs
    if "error" in exp:
        if rep.get("error") != exp["error"]:
            errs.append(f"{where}: expected error {exp['error']}, got {rep}")
        return errs
    if "code" in exp:
        if rep.get("code") != exp["code"] or "snapshot" in rep:
            errs.append(f"{where}: expected error code {exp['code']}, got {rep}")
        return errs
    if "need" in rep:
        return [f"{where}: unexpected need {rep['need']}"]
    if "error" in rep:
        return [f"{where}: unexpected error {rep}"]
    s = snap_from_json(rep["snapshot"])
    if exp.get("unchanged"):
        if canon(rep["snapshot"]) != canon(before, order=True) or rep["actions"] != []:
            errs.append(f"{where}: expected unchanged, got {rep}")
    if "snapshot" in exp and canon(rep["snapshot"]) != canon(exp["snapshot"], order=True):
        # exact: the field order of section 8, defaults omitted (no empty `pending`); the key
        # order INSIDE `resume` and `timers` is not specified and is ignored
        errs.append(f"{where}: snapshot={canon(rep['snapshot'])}, expected {canon(exp['snapshot'], order=True)}")
    if "pending" in exp and s["pending"] != exp["pending"]:
        errs.append(f"{where}: pending={s['pending']}, expected {exp['pending']}")
    for k in ("at", "seq"):
        if k in exp and s[k] != exp[k]:
            errs.append(f"{where}: {k}={s[k]!r}, expected {exp[k]!r}")
    if "timers" in exp and s["timers"] != exp["timers"]:
        errs.append(f"{where}: timers={s['timers']}, expected {exp['timers']}")
    if "timers_excludes" in exp and set(s["timers"]) & exp["timers_excludes"]:
        errs.append(f"{where}: timers={s['timers']} still contain {exp['timers_excludes']}")
    if "back" in exp and s.get("back") != exp["back"]:
        errs.append(f"{where}: back={s.get('back')!r}, expected {exp['back']!r}")
    if "resume" in exp and s["resume"] != exp["resume"]:
        errs.append(f"{where}: resume={s['resume']}, expected {exp['resume']}")
    for k, v in exp.get("resume_has", {}).items():
        if s["resume"].get(k) != v:
            errs.append(f"{where}: resume[{k}]={s['resume'].get(k)!r}, expected {v!r}")
    if "actions" in exp and json.dumps(rep["actions"]) != json.dumps(exp["actions"]):
        # exact, key order included: {"call", "token"?, "detached"?} (section 8)
        errs.append(f"{where}: actions={json.dumps(rep['actions'])}, expected {json.dumps(exp['actions'])}")
    if "recovered" in exp and rep.get("recovered", False) != exp["recovered"]:
        errs.append(f"{where}: recovered={rep.get('recovered')}, expected {exp['recovered']}")
    return errs


class OracleAsEngine:
    """Answers protocol requests with the oracle (to validate the oracle
    against the hand-written expectations)."""

    def __init__(self, text, opts):
        self.bot = Bot(text, opts)

    def req(self, r):
        from oracle import RequestError
        try:
            if r["op"] == "start" and "event" in r:
                res = self.bot.start_with(r["event"], r.get("guards", {}), r.get("seq", 0))
                if res[0] == "need":
                    return {"need": sorted(res[1])}
                return {"snapshot": snap_to_json(res[1]), "actions": res[2]}
            if r["op"] == "start":
                s, a = self.bot.start()
                return {"snapshot": snap_to_json(s), "actions": a}
            if r["op"] == "restart":
                s, a = self.bot.restart(snap_from_json(r["snapshot"]))
                return {"snapshot": snap_to_json(s), "actions": a}
            res = self.bot.step(snap_from_json(r["snapshot"]), r["event"], r.get("token"), r.get("guards", {}))
        except RequestError as e:
            return {"error": e.text, "code": e.code}
        if res[0] == "need":
            return {"need": sorted(res[1])}
        _, s, a, rec = res
        out = {"snapshot": snap_to_json(s), "actions": a}
        if rec:
            out["recovered"] = True
        return out

    def close(self):
        pass


def run_case(c, mk):
    text = json.dumps(c["doc"], ensure_ascii=False)
    e = mk(text)
    errs = []
    try:
        if c["start"]:
            rep = e.req({"op": "start"})
            snap = rep["snapshot"]
            if "start_expect" in c:
                errs += check(c["start_expect"], None, rep, None, "start")
        else:
            snap = c["snapshot"]
        for i, (ev, tok, guards, exp) in enumerate(c["steps"]):
            if ev == "@restart":
                rep = e.req({"op": "restart", "snapshot": snap})
            elif ev.startswith("@start="):
                req = {"op": "start", "event": ev[len("@start="):]}
                if tok is not None:
                    req["seq"] = tok
                if guards is not None:
                    req["guards"] = guards
                rep = e.req(req)
            else:
                req = {"op": "step", "snapshot": snap, "event": ev}
                if tok is not None:
                    req["token"] = tok
                if guards is not None:
                    req["guards"] = guards
                rep = e.req(req)
                if exp.get("no_need") and "need" in rep:
                    errs.append(f"step {i} {ev}: engine asked need {rep['need']} although the "
                                f"winning variant is unconditional")
                    req["guards"] = dict(req.get("guards") or {}, **{g: False for g in rep["need"]})
                    rep = e.req(req)
            errs += check(exp, snap, rep, None, f"step {i} {ev}")
            if "snapshot" in rep:
                snap = rep["snapshot"]
    finally:
        e.close()
    return errs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine")
    ap.add_argument("--group", default="conformance")
    ap.add_argument("--mutate")
    ap.add_argument("--oracle-only", action="store_true")
    ap.add_argument("--oracle-opt", action="append", default=[], help="k=v Options for oracle run")
    ap.add_argument("--expect-red", action="store_true")
    args = ap.parse_args()
    opts = Options(**dict(kv.split("=", 1) for kv in args.oracle_opt))
    cases = [c for c in CASES if args.group == "all" or c["group"] == args.group]
    targets = []
    if not args.oracle_only:
        targets.append(("engine", lambda t: Engine(args.engine, t, mutate=args.mutate)))
    if not args.mutate:
        targets.append(("oracle", lambda t: OracleAsEngine(t, opts)))
    total = fails = steps = 0
    for name, mk in targets:
        for c in cases:
            errs = run_case(c, mk)
            total += 1
            steps += len(c["steps"]) + (1 if c["start"] else 0)
            status = "PASS" if not errs else "FAIL"
            if errs:
                fails += 1
            print(f"{status} [{name}] {c['group']}/{c['name']} -- {c['spec']}")
            for x in errs:
                print("    ", x)
    print(f"cases run: {total} (targets: {[t[0] for t in targets]}), requests: {steps}, failed: {fails}")
    if args.expect_red:
        print("EXPECT-RED:", "RED (good)" if fails else "STILL GREEN")
        sys.exit(0 if fails else 1)
    sys.exit(1 if fails or total == 0 else 0)


if __name__ == "__main__":
    main()

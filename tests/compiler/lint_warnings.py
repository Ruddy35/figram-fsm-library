#!/usr/bin/env python3
"""`figram check` warns about designer mistakes that
are valid documents, and names the root section "bot".

  python3 lint_warnings.py --figram BIN

For every case `figram check` must exit 0, print exactly "ok" on stdout and on
stderr only lines "warning: …"; every expected fragment must occur in some
warning, and documents without defects must produce no warning at all
(warnings never change the verdict: a document valid before stays valid).
With --expect-red the exit status is inverted (a negative control: run against a broken
`figram`, e.g. filter_bin.py with FILTER_DEFECT=drop-outcome-warnings, which must FAIL some case).
FC-07 cases are rejected documents: the root is called `bot`, never ``, and
"is declared more than once" is reported once per name."""

import argparse
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
C = {"conditions": {"c1": "c1", "c2": "c2"}, "calls": {"k": "k"}}


def d(bot):
    x = {"figram": 1}
    x.update(C)
    x["bot"] = bot
    return x


def ex(name):
    return json.load(open(os.path.join(REPO, "examples", name), encoding="utf-8"))


TIMERS = json.load(open(os.path.join(HERE, "t_timers.bot.json"), encoding="utf-8"))

# (name, document, fragments that must be warned about; [] = no warning at all)
CASES = [
    ("pizza", ex("pizza.bot.json"), []),
    ("features", ex("features.bot.json"), []),
    ("variant_after_unconditional", d({"initial": "a", "children": {
        "a": {"on": {"btn:x": [{"to": "b"}, {"if": "c1", "to": "c"}]}}, "b": {}, "c": {}}}),
     ["arrow `a` on `btn:x`: variant 2 never fires, variant 1 before it has no condition",
      "message `c` is unreachable"]),
    ("duplicate_guard_in_arrow", d({"initial": "a", "children": {
        "a": {"on": {"btn:x": [{"if": "c1", "to": "b"}, {"if": "c1", "to": "c"}, {"to": "b"}]}},
        "b": {}, "c": {}}}),
     ["condition `c1` is repeated, variant 2 never fires", "message `c` is unreachable"]),
    ("unreachable_message", d({"initial": "a", "children": {
        "a": {}, "lost": {"on": {"btn:x": [{"to": "a"}]}}}}),
     ["message `lost` is unreachable"]),
    ("resume_only_from_inside", d({"initial": "S", "children": {
        "S": {"initial": "x", "on": {"btn:back": [{"to": "S", "resume": True}]},
              "children": {"x": {"on": {"btn:y": [{"to": "y"}]}}, "y": {}}}}}),
     ["arrows with `resume` into `S` are drawn only from inside the section"]),
    ("resume_from_outside_ok", d({"initial": "S", "children": {
        "S": {"initial": "x", "on": {"cmd:help": [{"to": "h"}]},
              "children": {"x": {"on": {"btn:y": [{"to": "y"}]}}, "y": {}}},
        "h": {"on": {"btn:back": [{"to": "S", "resume": True}]}}}}), []),
    ("timer_arrow_on_non_owner", d({"initial": "a", "children": {
        "a": {"on": {"btn:b": [{"to": "b"}], "timer:t": [{"to": "z"}]}},
        "b": {"timers": {"t": 1000}, "on": {"btn:a": [{"to": "a"}]}}, "z": {}}}),
     ["arrow `timer:t` on `a` never fires", "message `z` is unreachable"]),
    ("timer_arrow_on_section_without_owner", d({"initial": "S", "children": {
        "S": {"initial": "a", "on": {"timer:t": [{"to": "o"}]},
              "children": {"a": {"on": {"btn:o": [{"to": "o"}]}}}},
        "o": {"timers": {"t": 5}, "on": {"btn:a": [{"to": "a"}]}}}}),
     ["arrow `timer:t` on `S` never fires"]),
    ("timer_arrow_on_section_with_owner_ok", d({"initial": "S", "children": {
        "S": {"initial": "a", "on": {"timer:t": [{"to": "o"}]},
              "children": {"a": {"timers": {"t": 5}}}},
        "o": {"on": {"btn:a": [{"to": "a"}]}}}}), []),
    ("same_timer_two_messages_ok", d({"initial": "a", "children": {
        "a": {"timers": {"t": 1}, "on": {"btn:b": [{"to": "b"}]}},
        "b": {"timers": {"t": 2}, "on": {"timer:t": [{"to": "a"}]}}}}), []),
    ("shadowed_section_guard_ok", d({"initial": "S", "children": {
        "S": {"initial": "a", "on": {"btn:x": [{"if": "c2", "to": "c"}]},
              "children": {"a": {"on": {"btn:x": [{"to": "b"}]}},
                           "b": {"on": {"btn:a": [{"to": "a"}]}}, "c": {}}}}}), []),
    ("selfloop_starves_sibling_timer", TIMERS,
     ["loop `timer:tick` on `wait` restarts the message's timers (`timeout`)"]),
    ("selfloop_shorter_sibling_ok", d({"initial": "w", "children": {
        "w": {"timers": {"tick": 1000, "fast": 10},
              "on": {"timer:tick": [{"to": "w"}], "timer:fast": [{"to": "g"}]}},
        "g": {"on": {"btn:w": [{"to": "w"}]}}}}), []),
]

# The warning about outcomes without an arrow (docs/format.md, section 3, "Warnings"): a variant
# with an action with outcomes leads to a message (or, without `to`, stays on one) where an outcome
# of that action, `failed` included, has no arrow on the message or its sections; variants with
# `resume` or `back` are not checked.  (name, document, wanted fragments, fragments that must NOT
# occur in any warning)
O = {"conditions": {"c1": "c1"},
     "calls": {"k": "k", "chk": {"text": "check", "outcomes": ["yes", "no"]},
               "ping": {"text": "ping", "outcomes": ["pong"], "every": True, "detached": True}}}


def d3(bot):
    x = {"figram": 1}
    x.update(O)
    x["bot"] = bot
    return x


ALL_CHK = {"call:chk:yes": [{"to": "a"}], "call:chk:no": [{"to": "a"}], "call:chk:failed": [{"to": "a"}]}
CASES += [
    ("outcome_target_lacks_arrows", d3({"initial": "a", "children": {
        "a": {"on": {"btn:x": [{"to": "w", "do": ["chk"]}]}},
        "w": {"on": {"call:chk:yes": [{"to": "a"}]}}}}),
     ["outcome `no` of action `chk` has no arrow on `w`", "outcome `failed` of action `chk` has no arrow on `w`",
      "the variant of `a` on `btn:x` waits for it there"], ["outcome `yes`"]),
    ("outcome_stay_on_message", d3({"initial": "a", "children": {
        "a": {"on": {"btn:x": [{"do": ["k", "chk"]}]}}}}),
     ["outcome `yes` of action `chk` has no arrow on `a`", "outcome `no` of action `chk` has no arrow on `a`",
      "outcome `failed` of action `chk` has no arrow on `a`"], []),
    ("outcome_every_background_checked", d3({"initial": "a", "children": {
        "a": {"on": {"btn:x": [{"do": ["ping"]}], "call:ping:pong": [{}]}}}}),
     ["outcome `failed` of action `ping` has no arrow on `a`"], ["outcome `pong`"]),
    ("outcome_timer_arrow_variant_checked", d3({"initial": "a", "children": {
        "a": {"timers": {"t": 5}, "on": {"timer:t": [{"do": ["chk"]}], "call:chk:yes": [{}],
                                         "call:chk:no": [{}]}}}}),
     ["outcome `failed` of action `chk` has no arrow on `a`: the variant of `a` on `timer:t`"], []),
    ("outcome_into_section_checks_landing", d3({"initial": "a", "children": {
        "a": {"on": {"btn:x": [{"to": "S", "do": ["chk"]}], **ALL_CHK}},
        "S": {"initial": "s1", "children": {"s1": {"on": {"btn:a": [{"to": "a"}]}}}}}}),
     ["outcome `yes` of action `chk` has no arrow on `s1`"], ["no arrow on `a`"]),
    ("outcome_section_stay_each_message", d3({"initial": "S", "children": {
        "S": {"initial": "a", "on": {"btn:x": [{"do": ["chk"]}]}, "children": {
            "a": {"on": dict(ALL_CHK, **{"btn:b": [{"to": "b"}]})}, "b": {"on": {"btn:a": [{"to": "a"}]}}}}}}),
     ["outcome `yes` of action `chk` has no arrow on `b`: the variant of `S` on `btn:x`"], ["no arrow on `a`"]),
    ("outcome_arrows_on_root_ok", d3({"initial": "a", "on": ALL_CHK, "children": {
        "a": {"on": {"btn:x": [{"to": "w", "do": ["chk"]}]}}, "w": {"on": {"btn:a": [{"to": "a"}]}}}}), []),
    ("outcome_arrows_on_section_ok", d3({"initial": "a", "children": {
        "a": {"on": {"btn:x": [{"to": "w", "do": ["chk"]}]}},
        "S": {"initial": "w", "on": ALL_CHK, "children": {"w": {}}}}}), []),
    ("outcome_conditional_arrow_counts_ok", d3({"initial": "a", "children": {
        "a": {"on": {"btn:x": [{"to": "w", "do": ["chk"]}]}},
        "w": {"on": {"call:chk:yes": [{"if": "c1", "to": "a"}], "call:chk:no": [{"if": "c1", "to": "a"}],
                     "call:chk:failed": [{"if": "c1", "to": "a"}]}}}}), []),
    ("outcome_resume_back_not_checked_ok", d3({"initial": "a", "on": {"btn:b": [{"to": "a", "back": True,
                                                                                 "do": ["chk"]}]},
                                              "children": {
        "a": {"on": {"btn:x": [{"to": "S", "resume": True, "do": ["chk"]}]}},
        "S": {"initial": "s1", "children": {"s1": {"on": {"btn:a": [{"to": "a"}]}}}}}}), []),
    ("outcome_plain_action_not_checked_ok", d3({"initial": "a", "children": {
        "a": {"on": {"btn:x": [{"do": ["k"]}, ]}}}}), []),
    # a timer arrow without a target on a section fires only on the messages that own the timer
    ("outcome_section_timer_stay_owner_only_ok", d3({"initial": "h", "on": {"timer:t": [{"do": ["chk"]}]},
                                                    "children": {
        "h": {"timers": {"t": 10}, "on": {"btn:g": [{"to": "g"}], "call:chk:yes": [{"to": "g"}],
                                          "call:chk:no": [{"to": "g"}], "call:chk:failed": [{"to": "g"}]}},
        "g": {"on": {"btn:h": [{"to": "h"}]}}}}), []),
    ("outcome_section_timer_stay_owner_checked", d3({"initial": "h", "on": {"timer:t": [{"do": ["chk"]}]},
                                                    "children": {
        "h": {"timers": {"t": 10}, "on": {"btn:g": [{"to": "g"}], "call:chk:yes": [{"to": "g"}]}},
        "g": {"on": {"btn:h": [{"to": "h"}]}}}}),
     ["outcome `no` of action `chk` has no arrow on `h`: the variant of `bot` on `timer:t`"], ["no arrow on `g`"]),
]

# The trap of a "please wait" message (all arrows drawn on it are outcome arrows): an arrow of a
# section leads from it to a message where an outcome has no arrow, and "back" or "resume" can
# return the user to it after the outcome was ignored there. The pizza example had it before the
# outcome arrows moved to the root.
def pizza_outcomes_on_placing():
    x = ex("pizza.bot.json")
    root = x["bot"]["on"]
    arrows = {e: root.pop(e) for e in list(root) if e.startswith("call:create_order:")}
    x["bot"]["children"]["order"]["children"]["placing"] = {"on": arrows}
    return x


HELP = {"cmd:help": [{"to": "h"}]}
CASES += [
    ("wait_trap_pizza_outcomes_on_placing", pizza_outcomes_on_placing(),
     ["outcome `created` of action `create_order` has no arrow on `menu`, where `cmd:start` leads from "
      "`placing`, which waits for it", "can return the user to `placing` with nothing to wait for"], []),
    ("wait_trap_back", d3({"initial": "a", "on": HELP, "children": {
        "a": {"on": {"btn:x": [{"to": "w", "do": ["chk"]}]}},
        "w": {"on": ALL_CHK},
        "h": {"on": {"btn:back": [{"to": "a", "back": True}]}}}}),
     ["outcome `yes` of action `chk` has no arrow on `h`, where `cmd:help` leads from `w`"], []),
    ("wait_trap_resume", d3({"initial": "a", "on": HELP, "children": {
        "a": {"on": {"btn:x": [{"to": "S", "do": ["chk"]}]}},
        "S": {"initial": "w", "children": {"w": {"on": ALL_CHK}}},
        "h": {"on": {"btn:back": [{"to": "S", "resume": True}]}}}}),
     ["outcome `yes` of action `chk` has no arrow on `h`, where `cmd:help` leads from `w`"], []),
    # no way back to the waiting message: an ignored outcome does not trap the user
    ("wait_trap_no_return_ok", d3({"initial": "a", "on": HELP, "children": {
        "a": {"on": {"btn:x": [{"to": "w", "do": ["chk"]}]}},
        "w": {"on": ALL_CHK},
        "h": {"on": {"btn:back": [{"to": "a"}]}}}}), []),
    # the waiting message has a button of its own: the user is not stuck on it
    ("wait_trap_message_with_buttons_ok", d3({"initial": "a", "on": HELP, "children": {
        "a": {"on": {"btn:x": [{"to": "w", "do": ["chk"]}]}},
        "w": {"on": dict(ALL_CHK, **{"btn:x": [{"do": ["chk"]}]})},
        "h": {"on": {"btn:back": [{"to": "a", "back": True}]}}}}), []),
    # the outcome arrows on the root: handled wherever the user is
    ("wait_trap_outcomes_on_root_ok", d3({"initial": "a", "on": dict(HELP, **ALL_CHK), "children": {
        "a": {"on": {"btn:x": [{"to": "w", "do": ["chk"]}]}},
        "w": {},
        "h": {"on": {"btn:back": [{"to": "a", "back": True}]}}}}), []),
]

# FC-07: rejected documents with the root section in the messages
FC07 = [
    ("fc07_root_initial_and_empty_child", '{"figram":1,"bot":{"initial":"b","children":{"a":{},"":{}}}}'),
    ("fc07_dup_name", '{"figram":1,"bot":{"initial":"a","children":{"a":{},"s":{"initial":"a","children":'
                      '{"a":{}}}}}}'),
]


def run(figram, text):
    with tempfile.NamedTemporaryFile("w", suffix=".bot.json", delete=False) as f:
        f.write(text)
    p = subprocess.run([figram, "check", f.name], capture_output=True, text=True, timeout=120)
    return p.returncode, p.stdout, p.stderr


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--figram", required=True)
    ap.add_argument("--expect-red", action="store_true")
    a = ap.parse_args()
    fails = 0
    for entry in CASES:
        name, doc, want = entry[:3]
        absent = entry[3] if len(entry) > 3 else []
        rc, out, err = run(a.figram, json.dumps(doc, ensure_ascii=False))
        lines = [ln for ln in err.splitlines() if ln.strip()]
        ws = [ln[len("warning: "):] for ln in lines if ln.startswith("warning: ")]
        errs = []
        if rc != 0 or out.strip() != "ok":
            errs.append(f"verdict changed: rc={rc} stdout={out.strip()!r}")
        if len(ws) != len(lines):
            errs.append(f"stderr has non-warning lines: {lines}")
        if not want and ws:
            errs.append(f"unexpected warnings: {ws}")
        for frag in want:
            if not any(frag in w for w in ws):
                errs.append(f"missing warning containing {frag!r}; got {ws}")
        for frag in absent:
            if any(frag in w for w in ws):
                errs.append(f"warning containing {frag!r} not expected; got {ws}")
        fails += bool(errs)
        print(json.dumps({"case": name, "ok": not errs, "warnings": ws, "problems": errs},
                         ensure_ascii=False))
    for name, text in FC07:
        rc, out, err = run(a.figram, text)
        errs = []
        if rc == 0:
            errs.append("accepted")
        if "``" in out:
            errs.append("root shown as ``")
        dup = [ln for ln in out.splitlines() if "is declared more than once" in ln]
        if len(dup) != len(set(dup)):
            errs.append(f"duplicate messages {dup}")
        if "warning" in err:
            errs.append("warnings printed for a rejected document")
        fails += bool(errs)
        print(json.dumps({"case": name, "ok": not errs, "stdout": out.splitlines(), "problems": errs},
                         ensure_ascii=False))
    total = len(CASES) + len(FC07)
    print(f"cases {total}, failed {fails}")
    if a.expect_red:
        print("EXPECT-RED:", "RED (good)" if fails else "STILL GREEN")
        sys.exit(0 if fails else 1)
    sys.exit(1 if fails or not total else 0)


if __name__ == "__main__":
    main()

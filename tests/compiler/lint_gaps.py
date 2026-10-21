#!/usr/bin/env python3
"""Documents with designer mistakes that `figram check` used to accept
silently.  For every case the script (1) runs `figram check`, (2) proves the
defect is real with the engine (the arrow/variant is unreachable, or a guard
is requested although it cannot matter).

  python3 lint_gaps.py --figram BIN --engine BIN

Prints one line per case: check verdict + engine evidence.  Exit 0 when every
case behaves as expected (accepted by check, defect observable) and `check`
warns about each defect on stderr (CC05=fixed, the default; CC05=baseline
expects the silent acceptance of the baseline, commit d69bbaf)."""

import argparse
import json
import os
import subprocess
import tempfile

from engine import Engine

C = {"conditions": {"c1": "c1", "c2": "c2"}}


def d(bot):
    x = {"figram": 1}
    x.update(C)
    x["bot"] = bot
    return x


CASES = {
    # timer:X arrow on a message that does not own X (X exists on another message)
    "timer_arrow_on_non_owner": d({"initial": "a", "children": {
        "a": {"on": {"btn:b": [{"to": "b"}], "timer:t": [{"to": "z"}]}},
        "b": {"timers": {"t": 1000}}, "z": {}}}),
    # the same timer id on two messages (allowed; behaviour verified in directed.py)
    "same_timer_two_messages": d({"initial": "a", "children": {
        "a": {"timers": {"t": 1}, "on": {"btn:b": [{"to": "b"}]}},
        "b": {"timers": {"t": 2}, "on": {"timer:t": [{"to": "a"}]}}}}),
    # resume arrow into the section it is drawn on / from inside: always "stay here"
    "resume_only_from_inside": d({"initial": "S", "children": {
        "S": {"initial": "x", "on": {"btn:back": [{"to": "S", "resume": True}]},
              "children": {"x": {"on": {"btn:y": [{"to": "y"}]}}, "y": {}}}}}),
    # variant after an unconditional variant in the same arrow (dead)
    "variant_after_unconditional": d({"initial": "a", "children": {
        "a": {"on": {"btn:x": [{"to": "b"}, {"if": "c1", "to": "c"}]}}, "b": {}, "c": {}}}),
    # the same condition twice in one arrow: the second can never win
    "duplicate_guard_in_arrow": d({"initial": "a", "children": {
        "a": {"on": {"btn:x": [{"if": "c1", "to": "b"}, {"if": "c1", "to": "c"}]}}, "b": {}, "c": {}}}),
    # guarded section variant shadowed by an unconditional message variant: engine still needs c2
    "need_for_shadowed_section_guard": d({"initial": "S", "children": {
        "S": {"initial": "a", "on": {"btn:x": [{"if": "c2", "to": "c"}]},
              "children": {"a": {"on": {"btn:x": [{"to": "b"}]}}, "b": {}, "c": {}}}}}),
    # message nobody can reach (not initial, no arrow into it)
    "unreachable_message": d({"initial": "a", "children": {"a": {}, "lost": {"on": {"btn:x": [{"to": "a"}]}}}}),
}


def check(figram, doc):
    with tempfile.NamedTemporaryFile("w", suffix=".bot.json", delete=False) as f:
        json.dump(doc, f)
    p = subprocess.run([figram, "check", f.name], capture_output=True, text=True, timeout=60)
    return p.returncode, p.stdout.strip(), [ln for ln in p.stderr.splitlines() if ln.strip()]


# CC05=fixed (default): `check` still accepts (exit 0, "ok") but warns on stderr;
# CC05=baseline: the original observation (accepted silently).
WARN = {
    "timer_arrow_on_non_owner": "arrow `timer:t` on `a` never fires",
    "same_timer_two_messages": None,
    "resume_only_from_inside": "drawn only from inside the section",
    "variant_after_unconditional": "variant 2 never fires",
    "duplicate_guard_in_arrow": "condition `c1` is repeated",
    "need_for_shadowed_section_guard": None,
    "unreachable_message": "message `lost` is unreachable",
}


def warned_ok(name, warnings):
    if os.environ.get("CC05") == "baseline":
        return warnings == []
    frag = WARN[name]
    if frag is None:
        return warnings == []
    return any(w.startswith("warning: ") and frag in w for w in warnings)


def evidence(engine, name, doc):
    e = Engine(engine, json.dumps(doc))
    try:
        if name == "timer_arrow_on_non_owner":
            # the only way timer:t is accepted on a is a snapshot entry a never creates
            s = e.req({"op": "start"})["snapshot"]
            r = e.req({"op": "step", "snapshot": s, "event": "timer:t", "token": s.get("seq", 0)})
            forged = dict(s, timers={"t": 5})
            r2 = e.req({"op": "step", "snapshot": forged, "event": "timer:t", "token": 5})
            return {"start_timers": s.get("timers", {}), "fire_on_a": r["actions"],
                    "fire_with_foreign_entry": r2["snapshot"]["at"]}
        if name == "same_timer_two_messages":
            s = e.req({"op": "start"})["snapshot"]
            r = e.req({"op": "step", "snapshot": s, "event": "btn:b"})
            old = e.req({"op": "step", "snapshot": r["snapshot"], "event": "timer:t", "token": 1})
            new = e.req({"op": "step", "snapshot": r["snapshot"], "event": "timer:t", "token": 2})
            return {"a->b": r["actions"], "old_token": old["actions"], "new_token_at": new["snapshot"]["at"]}
        if name == "resume_only_from_inside":
            s = e.req({"op": "start"})["snapshot"]
            r = e.req({"op": "step", "snapshot": s, "event": "btn:y"})
            b = e.req({"op": "step", "snapshot": r["snapshot"], "event": "btn:back"})
            return {"at_before": r["snapshot"]["at"], "back_goes_to": b["snapshot"]["at"], "actions": b["actions"]}
        if name in ("variant_after_unconditional", "duplicate_guard_in_arrow"):
            s = e.req({"op": "start"})["snapshot"]
            outs = set()
            for v in (True, False):
                r = e.req({"op": "step", "snapshot": s, "event": "btn:x", "guards": {"c1": v}})
                outs.add(r["snapshot"]["at"])
            return {"targets_reached_for_all_c1": sorted(outs)}
        if name == "need_for_shadowed_section_guard":
            s = e.req({"op": "start"})["snapshot"]
            r = e.req({"op": "step", "snapshot": s, "event": "btn:x"})
            return {"engine_reply": r}
        if name == "unreachable_message":
            return {"states": e.states}
    finally:
        e.close()


EXPECT = {
    # CC01=fixed (default): a foreign snapshot entry no longer fires the arrow on a non-owner (stays on a);
    # CC01=baseline: the original observation (the arrow fires, user moves to z).
    "timer_arrow_on_non_owner": lambda ev: ev["fire_on_a"] == [] and ev["fire_with_foreign_entry"] == (
        "z" if os.environ.get("CC01") == "baseline" else "a"),
    "same_timer_two_messages": lambda ev: ev["old_token"] == [] and ev["new_token_at"] == "a",
    "resume_only_from_inside": lambda ev: ev["back_goes_to"] == ev["at_before"],
    "variant_after_unconditional": lambda ev: ev["targets_reached_for_all_c1"] == ["b"],
    "duplicate_guard_in_arrow": lambda ev: "c" not in ev["targets_reached_for_all_c1"],
    # CC04=fixed (default): the shadowed guard is not requested, the step goes to b;
    # CC04=baseline: the original observation (need ["c2"]).
    "need_for_shadowed_section_guard": lambda ev: (
        ev["engine_reply"].get("need") == ["c2"] if os.environ.get("CC04") == "baseline"
        else "need" not in ev["engine_reply"] and ev["engine_reply"]["snapshot"]["at"] == "b"),
    "unreachable_message": lambda ev: ev["states"] == 2,
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--figram", required=True)
    ap.add_argument("--engine", required=True)
    args = ap.parse_args()
    ok_all = True
    for name, doc in CASES.items():
        rc, out, warnings = check(args.figram, doc)
        ev = evidence(args.engine, name, doc)
        good = rc == 0 and out == "ok" and warned_ok(name, warnings) and EXPECT[name](ev)
        ok_all &= good
        print(json.dumps({"case": name, "figram_check": [rc, out, warnings], "engine_evidence": ev,
                          "as_documented": good}, ensure_ascii=False))
    print("cases:", len(CASES), "as documented:", ok_all)
    raise SystemExit(0 if ok_all else 1)


if __name__ == "__main__":
    main()

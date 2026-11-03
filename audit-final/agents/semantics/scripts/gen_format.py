#!/usr/bin/env python3
"""Generate small documents for `figram check`, each with the verdict expected from the prose
(E = error, exit 1; OK = accepted). Writes REPRODUCTIONS/format/<name>.bot.json and cases.json."""
import json, copy, os
OUT = "/home/user/figram-audit/agents5/semantics/REPRODUCTIONS/format"
BASE = {"figram": 1,
        "calls": {"log": "Log", "mk": {"text": "Make", "outcomes": ["ok"]}},
        "bot": {"initial": "h",
                "on": {"call:mk:ok": [{"to": "h"}], "call:mk:failed": [{"to": "h"}]},
                "children": {"h": {"on": {"btn:go": [{"do": ["mk"]}]}}}}}
cases = []
def case(name, verdict, why, calls=None, root_on=None, h_on=None, raw=None):
    d = copy.deepcopy(BASE)
    if calls: d["calls"].update(calls)
    if root_on: d["bot"]["on"].update(root_on)
    if h_on: d["bot"]["children"]["h"]["on"].update(h_on)
    text = raw if raw is not None else json.dumps(d, indent=1)
    open(f"{OUT}/{name}.bot.json", "w").write(text)
    cases.append({"name": name, "expect": verdict, "why": why})
case("F00_base", "OK", "base document")
case("F01_empty_outcome", "E", "format.md:123 outcomes are non-empty names", calls={"x": {"text": "X", "outcomes": [""]}})
case("F02_colon_outcome", "E", "format.md:123 names without `:`", calls={"x": {"text": "X", "outcomes": ["a:b"]}})
case("F03_failed_listed", "E", "format.md:123-124 other than `failed`", calls={"x": {"text": "X", "outcomes": ["failed"]}})
case("F04_dup_outcome", "E", "format.md:123 unique", calls={"x": {"text": "X", "outcomes": ["a", "a"]}})
case("F05_every_without_outcomes", "E", "format.md:124 every only with outcomes", calls={"x": {"text": "X", "every": True}})
case("F06_every_false_no_outcomes", "OK", "format.md:82 explicit default every:false", calls={"x": {"text": "X", "every": False}})
case("F07_detached_false", "OK", "format.md:82 explicit default detached:false", calls={"x": {"text": "X", "detached": False}})
case("F08_outcomes_empty", "OK", "format.md:26-28 explicit default value (empty list = no outcomes)", calls={"x": {"text": "X", "outcomes": []}})
case("F09_every_outcomes_empty", "E", "format.md:124 every only with outcomes", calls={"x": {"text": "X", "outcomes": [], "every": True}})
case("F10_twice_in_do", "E", "format.md:116 V6", h_on={"btn:two": [{"do": ["mk", "log", "mk"]}]})
case("F11_two_outcome_actions", "OK", "V6 is per action", calls={"mk2": {"text": "M2", "outcomes": ["ok"]}},
     root_on={"call:mk2:ok": [{"to": "h"}], "call:mk2:failed": [{"to": "h"}]}, h_on={"btn:two": [{"do": ["mk", "mk2"]}]})
case("F12_plain_twice", "OK", "V6 only for actions with outcomes", h_on={"btn:two": [{"do": ["log", "log"]}]})
case("F13_undeclared_outcome", "E", "format.md:114-115 V5", h_on={"call:mk:zzz": [{"to": "h"}]})
case("F14_action_without_outcomes", "E", "V5: an action with outcomes", h_on={"call:log:ok": [{"to": "h"}]})
case("F15_undeclared_action", "E", "V5", h_on={"call:nope:ok": [{"to": "h"}]})
case("F16_no_outcome_part", "E", "format.md:213-215 prefix call: reserved", h_on={"call:mk": [{"to": "h"}]})
case("F17_empty_action", "E", "reserved prefix / V5", h_on={"call::ok": [{"to": "h"}]})
case("F18_empty_outcome_event", "E", "reserved prefix / V5", h_on={"call:mk:": [{"to": "h"}]})
case("F20_colon_action", "OK", "format.md:214-215 action name may contain `:`",
     calls={"a:b": {"text": "AB", "outcomes": ["ok"]}}, h_on={"call:a:b:ok": [{"to": "h"}], "call:a:b:failed": [{"to": "h"}], "btn:ab": [{"do": ["a:b"]}]})
case("F21_colon_action_short", "E", "split at last `:` -> action a, outcome b",
     calls={"a:b": {"text": "AB", "outcomes": ["ok"]}}, h_on={"call:a:b": [{"to": "h"}]})
case("F22_bare_prefix", "E", "reserved prefix", h_on={"call:": [{"to": "h"}]})
case("F23a_outcomes_string", "E", "grammar [Name]", calls={"x": {"text": "X", "outcomes": "ok"}})
case("F23b_outcomes_number", "E", "grammar [Name]", calls={"x": {"text": "X", "outcomes": [1]}})
case("F23c_detached_string", "E", "grammar true", calls={"x": {"text": "X", "detached": "yes"}})
case("F23d_every_number", "E", "grammar true", calls={"x": {"text": "X", "outcomes": ["a"], "every": 1}})
case("F24_unknown_call_key", "E", "format.md:236-243 unknown key in an action", calls={"x": {"text": "X", "outcome": ["a"]}})
case("F25_call_annotation", "OK", "format.md:221-222 action takes annotations", calls={"x": {"text": "X", "x-note": {"any": 1}}})
case("F26a_resume_no_to", "E", "format.md:112 V3 resume needs to", h_on={"btn:r": [{"resume": True}]})
case("F26b_back_no_to", "E", "format.md:112 V3 back needs to", h_on={"btn:r": [{"back": True}]})
case("F27_empty_variant", "OK", "grammar: all variant fields optional", h_on={"btn:nop": [{}]})
case("F29_call_without_text", "E", "grammar Call requires text", calls={"x": {"outcomes": ["a"]}})
case("F30_empty_action_name", "E", "format.md:210 names non-empty", calls={"": "Empty"})
case("F32_prefix_actions", "OK", "a/b and a:b/c are distinct after split at last `:`",
     calls={"a": {"text": "A", "outcomes": ["b"]}, "a:b": {"text": "AB", "outcomes": ["c"]}},
     h_on={"call:a:b": [{"to": "h"}], "call:a:b:c": [{"to": "h"}]})
case("F33_action_ending_colon", "OK", "action `a:` outcome ok -> call:a::ok", calls={"a:": {"text": "A", "outcomes": ["ok"]}},
     h_on={"call:a::ok": [{"to": "h"}]})
case("F34_empty_timer_event", "E", "V5 timer:<name> existing timer", h_on={"timer:": [{"to": "h"}]})
case("F38_space_outcome", "OK", "non-empty name without `:`", calls={"x": {"text": "X", "outcomes": [" "]}})
case("F39_every_detached", "OK", "properties combine", calls={"x": {"text": "X", "outcomes": ["a"], "every": True, "detached": True}})
case("F41_twice_across_variants", "OK", "V6 is per variant", h_on={"btn:v": [{"if": "c", "do": ["mk"]}, {"do": ["mk"]}]}, )
case("F44_outcomes_null", "E", "grammar [Name]", calls={"x": {"text": "X", "outcomes": None}})
case("F45_detached_null", "E", "grammar true", calls={"x": {"text": "X", "detached": None}})
case("F46_every_true_null_outcomes", "E", "grammar", calls={"x": {"text": "X", "every": True, "outcomes": None}})
case("F49_outcome_named_like_failed_case", "OK", "only exact `failed` is reserved", calls={"x": {"text": "X", "outcomes": ["Failed"]}})
case("F50_call_event_on_section", "OK", "outcome arrows anywhere", root_on={"call:mk:failed": [{"do": ["log"]}]})
case("F51_stay_with_if", "OK", "variant with if only", h_on={"btn:c": [{"if": "c"}]})
case("F52_to_null", "E", "grammar to: Name", h_on={"btn:n": [{"to": None}]})
case("F53_resume_false_no_to", "OK", "explicit default resume:false without to", h_on={"btn:n": [{"resume": False, "back": False, "do": ["log"]}]})
# F41/F51 need the condition declared
for c in cases:
    if c["name"] in ("F41_twice_across_variants", "F51_stay_with_if"):
        p = f"{OUT}/{c['name']}.bot.json"; d = json.load(open(p)); d["conditions"] = {"c": "C"}; open(p, "w").write(json.dumps(d, indent=1))
json.dump(cases, open(f"{OUT}/cases.json", "w"), indent=1)
print(len(cases), "cases")

#!/usr/bin/env python3
"""P-24 / CC-05 / ET-05 / FC-07: `figram check` warns about designer mistakes that
are valid documents, and names the root section «bot».

  python3 lint_warnings.py --figram BIN

For every case `figram check` must exit 0, print exactly "ok" on stdout and on
stderr only lines "предупреждение: …"; every expected fragment must occur in some
warning, and documents without defects must produce no warning at all
(warnings never change the verdict: a document valid before stays valid).
FC-07 cases are rejected documents: the root is called «bot», never «», and
«объявлен несколько раз» is reported once per name."""

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
     ["Стрелка «a» по «btn:x»: вариант 2 никогда не сработает — перед ним вариант 1 без условия",
      "Сообщение «c» недостижимо"]),
    ("duplicate_guard_in_arrow", d({"initial": "a", "children": {
        "a": {"on": {"btn:x": [{"if": "c1", "to": "b"}, {"if": "c1", "to": "c"}, {"to": "b"}]}},
        "b": {}, "c": {}}}),
     ["условие «c1» повторяется, вариант 2 никогда не сработает", "Сообщение «c» недостижимо"]),
    ("unreachable_message", d({"initial": "a", "children": {
        "a": {}, "lost": {"on": {"btn:x": [{"to": "a"}]}}}}),
     ["Сообщение «lost» недостижимо"]),
    ("resume_only_from_inside", d({"initial": "S", "children": {
        "S": {"initial": "x", "on": {"btn:back": [{"to": "S", "resume": True}]},
              "children": {"x": {"on": {"btn:y": [{"to": "y"}]}}, "y": {}}}}}),
     ["«Вернуться туда, где был» в «S» нарисовано только изнутри раздела"]),
    ("resume_from_outside_ok", d({"initial": "S", "children": {
        "S": {"initial": "x", "on": {"cmd:help": [{"to": "h"}]},
              "children": {"x": {"on": {"btn:y": [{"to": "y"}]}}, "y": {}}},
        "h": {"on": {"btn:back": [{"to": "S", "resume": True}]}}}}), []),
    ("timer_arrow_on_non_owner", d({"initial": "a", "children": {
        "a": {"on": {"btn:b": [{"to": "b"}], "timer:t": [{"to": "z"}]}},
        "b": {"timers": {"t": 1000}, "on": {"btn:a": [{"to": "a"}]}}, "z": {}}}),
     ["Стрелка «timer:t» на «a» никогда не сработает", "Сообщение «z» недостижимо"]),
    ("timer_arrow_on_section_without_owner", d({"initial": "S", "children": {
        "S": {"initial": "a", "on": {"timer:t": [{"to": "o"}]},
              "children": {"a": {"on": {"btn:o": [{"to": "o"}]}}}},
        "o": {"timers": {"t": 5}, "on": {"btn:a": [{"to": "a"}]}}}}),
     ["Стрелка «timer:t» на «S» никогда не сработает"]),
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
     ["Петля «timer:tick» на «wait» перезапускает таймеры сообщения («timeout»)"]),
    ("selfloop_shorter_sibling_ok", d({"initial": "w", "children": {
        "w": {"timers": {"tick": 1000, "fast": 10},
              "on": {"timer:tick": [{"to": "w"}], "timer:fast": [{"to": "g"}]}},
        "g": {"on": {"btn:w": [{"to": "w"}]}}}}), []),
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
    a = ap.parse_args()
    fails = 0
    for name, doc, want in CASES:
        rc, out, err = run(a.figram, json.dumps(doc, ensure_ascii=False))
        lines = [ln for ln in err.splitlines() if ln.strip()]
        ws = [ln[len("предупреждение: "):] for ln in lines if ln.startswith("предупреждение: ")]
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
        fails += bool(errs)
        print(json.dumps({"case": name, "ok": not errs, "warnings": ws, "problems": errs},
                         ensure_ascii=False))
    for name, text in FC07:
        rc, out, err = run(a.figram, text)
        errs = []
        if rc == 0:
            errs.append("accepted")
        if "«»" in out:
            errs.append("root shown as «»")
        dup = [ln for ln in out.splitlines() if "объявлен несколько раз" in ln]
        if len(dup) != len(set(dup)):
            errs.append(f"duplicate messages {dup}")
        if "предупреждение" in err:
            errs.append("warnings printed for a rejected document")
        fails += bool(errs)
        print(json.dumps({"case": name, "ok": not errs, "stdout": out.splitlines(), "problems": errs},
                         ensure_ascii=False))
    total = len(CASES) + len(FC07)
    print(f"cases {total}, failed {fails}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""(d) Engine request table: small hand-written request lines, exact replies.

Document: examples/features.bot.json (guard is_vip on home/btn:go, timer
«remind» on item with a handler, timer «idle» on home without one).
Every request is a raw text line (so 1.0 / 1e0 / -0 / duplicate keys reach
the engine as written).  The expected reply column is the exact byte string;
it was recorded from the baseline binary and then reviewed by hand against
FORMAT.md §7 — the column `why` says which rule each reply follows.

Rows already reported by the engine-timers agent are tagged ET-07 / ET-12 and
are kept only as context; rows tagged FC-xx are new.

    python3 d_engine_requests.py [--break flip|lax-id] [--record]
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import ENGINE, ROOT, run  # noqa: E402

DOC = os.path.join(ROOT, "examples", "features.bot.json")
H = '{"at":"home"}'
ITEM = '{"at":"item","timers":{"remind":5},"seq":5}'

# (row id, request line, expected reply, tag, why)
ROWS = [
    # ---- id of every JSON type -----------------------------------------
    ("id-absent", '{"op":"hello"}', '{"id":null,"protocol":1,"states":8}', "", "missing id -> null"),
    ("id-null", '{"id":null,"op":"hello"}', '{"id":null,"protocol":1,"states":8}', "", ""),
    ("id-true", '{"id":true,"op":"hello"}', '{"id":true,"protocol":1,"states":8}', "", "any JSON echoed"),
    ("id-str", '{"id":"abc","op":"hello"}', '{"id":"abc","protocol":1,"states":8}', "", ""),
    ("id-str-esc", '{"id":"\\u00e9\\ud83d\\ude00\\u0000\\/","op":"hello"}',
     '{"id":"é😀\\u0000/","protocol":1,"states":8}', "ET-07", "echoed by value: escapes re-spelled"),
    ("id-neg-zero", '{"id":-0,"op":"hello"}', '{"id":0,"protocol":1,"states":8}', "ET-07", "by value"),
    ("id-1.5e3", '{"id":1.5e3,"op":"hello"}', '{"id":15e2,"protocol":1,"states":8}', "ET-07", "by value"),
    ("id-1E2", '{"id":1E2,"op":"hello"}', '{"id":1e2,"protocol":1,"states":8}', "ET-07", ""),
    ("id-0.1e1", '{"id":0.1e1,"op":"hello"}', '{"id":1,"protocol":1,"states":8}', "ET-07", ""),
    ("id-1.10", '{"id":1.10,"op":"hello"}', '{"id":1.10,"protocol":1,"states":8}', "", ""),
    ("id-bigint", '{"id":12345678901234567890123,"op":"hello"}',
     '{"id":12345678901234567890123,"protocol":1,"states":8}', "", "exact"),
    ("id-1e400", '{"id":1e400,"op":"hello"}', '{"id":1e400,"protocol":1,"states":8}', "", ""),
    ("id-array", '{"id":[1,{"a":2}],"op":"hello"}', '{"id":[1,{"a":2}],"protocol":1,"states":8}', "", ""),
    ("id-object", '{"id":{},"op":"hello"}', '{"id":{},"protocol":1,"states":8}', "", ""),
    ("id-after-op", '{"op":"hello","id":7}', '{"id":7,"protocol":1,"states":8}', "", "key order irrelevant"),
    ("id-dup", '{"id":1,"id":2,"op":"hello"}', '{"id":null,"error":"повторяющийся ключ «id»"}', "ET-07", ""),
    # ---- unknown / misspelt top-level fields ---------------------------
    ("extra-on-hello", '{"id":1,"op":"hello","extra":1,"snapshot":"garbage"}',
     '{"id":1,"protocol":1,"states":8}', "FC-04", "unknown fields ignored"),
    ("typo-gaurds", '{"id":2,"op":"step","snapshot":' + H + ',"event":"btn:go","gaurds":{"is_vip":true}}',
     '{"id":2,"need":["is_vip"]}', "FC-04", "misspelt guards ignored -> need forever"),
    ("typo-guard", '{"id":3,"op":"step","snapshot":' + H + ',"event":"btn:go","guard":{"is_vip":true}}',
     '{"id":3,"need":["is_vip"]}', "FC-04", ""),
    ("typo-evnt", '{"id":4,"op":"step","snapshot":' + H + ',"evnt":"btn:go"}',
     '{"id":4,"error":"нужно поле «event»"}', "", "required field caught"),
    ("start-with-junk", '{"id":5,"op":"start","event":"x","guards":5,"snapshot":null}',
     '{"id":5,"snapshot":{"at":"home","timers":{"idle":1},"seq":1},"actions":[{"start_timer":"idle","ms":60000,"token":1},{"send":"home"}]}',
     "FC-04", "start ignores everything"),
    ("op-case", '{"id":6,"op":"HELLO"}', '{"id":6,"error":"неизвестная операция «op»"}', "", ""),
    ("op-missing", '{"id":7}', '{"id":7,"error":"неизвестная операция «op»"}', "", ""),
    ("op-null", '{"id":8,"op":null}', '{"id":8,"error":"неизвестная операция «op»"}', "", ""),
    ("not-object", '[1]', '{"id":null,"error":"запрос должен быть объектом"}', "", ""),
    # ---- guards ---------------------------------------------------------
    ("g-true", '{"id":10,"op":"step","snapshot":' + H + ',"event":"btn:go","guards":{"is_vip":true}}',
     '{"id":10,"snapshot":{"at":"vip","seq":1},"actions":[{"cancel_timer":"idle"},{"send":"vip"}]}', "",
     "exit(home) cancels idle"),
    ("g-false", '{"id":11,"op":"step","snapshot":' + H + ',"event":"btn:go","guards":{"is_vip":false}}',
     '{"id":11,"snapshot":{"at":"catalog","resume":{"shop":"catalog"},"seq":1},"actions":[{"cancel_timer":"idle"},{"send":"catalog"}]}',
     "", "shop -> catalog, place remembered"),
    ("g-empty", '{"id":12,"op":"step","snapshot":' + H + ',"event":"btn:go","guards":{}}',
     '{"id":12,"need":["is_vip"]}', "", "rule 3"),
    ("g-absent", '{"id":13,"op":"step","snapshot":' + H + ',"event":"btn:go"}',
     '{"id":13,"need":["is_vip"]}', "", ""),
    ("g-1", '{"id":14,"op":"step","snapshot":' + H + ',"event":"btn:go","guards":{"is_vip":1}}',
     '{"id":14,"error":"условие «is_vip»: ожидалось true или false"}', "", ""),
    ("g-str", '{"id":15,"op":"step","snapshot":' + H + ',"event":"btn:go","guards":{"is_vip":"true"}}',
     '{"id":15,"error":"условие «is_vip»: ожидалось true или false"}', "", ""),
    ("g-null-val", '{"id":16,"op":"step","snapshot":' + H + ',"event":"btn:go","guards":{"is_vip":null}}',
     '{"id":16,"error":"условие «is_vip»: ожидалось true или false"}', "", ""),
    ("g-null", '{"id":17,"op":"step","snapshot":' + H + ',"event":"btn:go","guards":null}',
     '{"id":17,"error":"«guards» должен быть объектом"}', "", ""),
    ("g-array", '{"id":18,"op":"step","snapshot":' + H + ',"event":"btn:go","guards":[]}',
     '{"id":18,"error":"«guards» должен быть объектом"}', "", ""),
    ("g-dup", '{"id":19,"op":"step","snapshot":' + H + ',"event":"btn:go","guards":{"is_vip":true,"is_vip":false}}',
     '{"id":null,"error":"повторяющийся ключ «is_vip»"}', "ET-07", ""),
    ("g-unneeded-nonbool", '{"id":20,"op":"step","snapshot":' + H + ',"event":"btn:self","guards":{"other":1}}',
     '{"id":20,"error":"условие «other»: ожидалось true или false"}', "FC-04",
     "names not checked, but values of unknown names are"),
    ("g-unknown-names", '{"id":21,"op":"step","snapshot":' + H + ',"event":"btn:go","guards":{"":true,"zz":false,"is_vip":true}}',
     '{"id":21,"snapshot":{"at":"vip","seq":1},"actions":[{"cancel_timer":"idle"},{"send":"vip"}]}', "FC-04",
     "unknown names ignored"),
    # ---- token / timers -------------------------------------------------
    ("t-match", '{"id":30,"op":"step","snapshot":' + ITEM + ',"event":"timer:remind","token":5}',
     '{"id":30,"snapshot":{"at":"reminder","resume":{"shop":"reminder"},"seq":6},"actions":[{"cancel_timer":"remind"},{"send":"reminder"}]}',
     "", ""),
    ("t-5.0", '{"id":31,"op":"step","snapshot":' + ITEM + ',"event":"timer:remind","token":5.0}',
     '{"id":31,"snapshot":{"at":"reminder","resume":{"shop":"reminder"},"seq":6},"actions":[{"cancel_timer":"remind"},{"send":"reminder"}]}',
     "ET-12", "by value"),
    ("t-stale", '{"id":32,"op":"step","snapshot":' + ITEM + ',"event":"timer:remind","token":4}',
     '{"id":32,"snapshot":{"at":"item","timers":{"remind":5},"seq":5},"actions":[]}', "", "rule 2"),
    ("t-2^53+1", '{"id":33,"op":"step","snapshot":' + ITEM + ',"event":"timer:remind","token":9007199254740993}',
     '{"id":33,"snapshot":{"at":"item","timers":{"remind":5},"seq":5},"actions":[]}', "ET-12",
     "> 2^53 accepted, never matches"),
    ("t-str", '{"id":34,"op":"step","snapshot":' + ITEM + ',"event":"timer:remind","token":"5"}',
     '{"id":34,"error":"для события таймера нужно поле «token»"}', "ET-12", "misleading text"),
    ("t-neg", '{"id":35,"op":"step","snapshot":' + ITEM + ',"event":"timer:remind","token":-1}',
     '{"id":35,"error":"«token» должен быть неотрицательным целым"}', "", ""),
    ("t-1e19", '{"id":36,"op":"step","snapshot":' + ITEM + ',"event":"timer:remind","token":1e19}',
     '{"id":36,"error":"«token» должен быть неотрицательным целым"}', "ET-12", ""),
    ("t-missing", '{"id":37,"op":"step","snapshot":' + ITEM + ',"event":"timer:remind"}',
     '{"id":37,"error":"для события таймера нужно поле «token»"}', "", ""),
    ("t-on-named", '{"id":38,"op":"step","snapshot":' + ITEM + ',"event":"btn:other","token":"junk"}',
     '{"id":38,"snapshot":{"at":"item2","resume":{"shop":"item2"},"seq":6},"actions":[{"cancel_timer":"remind"},{"send":"item2"}]}',
     "", "token ignored for named events"),
    # ---- event ----------------------------------------------------------
    ("e-empty", '{"id":40,"op":"step","snapshot":' + H + ',"event":""}',
     '{"id":40,"snapshot":{"at":"home"},"actions":[]}', "", "rule 4"),
    ("e-timer-colon", '{"id":41,"op":"step","snapshot":' + ITEM + ',"event":"timer:","token":5}',
     '{"id":41,"snapshot":{"at":"item","timers":{"remind":5},"seq":5},"actions":[]}', "", "timer «» never active"),
    ("e-timer-colon-notoken", '{"id":42,"op":"step","snapshot":' + H + ',"event":"timer:"}',
     '{"id":42,"error":"для события таймера нужно поле «token»"}', "", ""),
    ("e-missing", '{"id":43,"op":"step","snapshot":' + H + '}', '{"id":43,"error":"нужно поле «event»"}', "", ""),
    ("e-num", '{"id":44,"op":"step","snapshot":' + H + ',"event":5}', '{"id":44,"error":"нужно поле «event»"}', "", ""),
    ("e-TIMER", '{"id":45,"op":"step","snapshot":' + ITEM + ',"event":"TIMER:remind","token":5}',
     '{"id":45,"snapshot":{"at":"item","timers":{"remind":5},"seq":5},"actions":[]}', "", "case-sensitive"),
    # ---- snapshot inside the request -------------------------------------
    ("s-dup-at", '{"id":50,"op":"step","snapshot":{"at":"home","at":"vip"},"event":"btn:self"}',
     '{"id":null,"error":"повторяющийся ключ «at»"}', "ET-07", ""),
    ("s-dup-timer", '{"id":51,"op":"step","snapshot":{"at":"item","timers":{"remind":5,"remind":6}},"event":"x"}',
     '{"id":null,"error":"повторяющийся ключ «remind»"}', "ET-07", ""),
    ("s-seq-1.0", '{"id":52,"op":"step","snapshot":{"at":"home","seq":1.0},"event":"btn:self"}',
     '{"id":52,"snapshot":{"at":"home","timers":{"idle":2},"seq":2},"actions":[{"cancel_timer":"idle"},{"start_timer":"idle","ms":60000,"token":2},{"send":"home"}]}',
     "", "seq by value"),
    ("s-seq-1e0", '{"id":53,"op":"step","snapshot":{"at":"home","seq":1e0},"event":"btn:self"}',
     '{"id":53,"snapshot":{"at":"home","timers":{"idle":2},"seq":2},"actions":[{"cancel_timer":"idle"},{"start_timer":"idle","ms":60000,"token":2},{"send":"home"}]}',
     "", ""),
    ("s-seq-neg-zero", '{"id":54,"op":"step","snapshot":{"at":"home","seq":-0},"event":"btn:self"}',
     '{"id":54,"snapshot":{"at":"home","timers":{"idle":1},"seq":1},"actions":[{"cancel_timer":"idle"},{"start_timer":"idle","ms":60000,"token":1},{"send":"home"}]}',
     "", ""),
    ("s-v-1.0", '{"id":55,"op":"step","snapshot":{"v":1.0,"at":"home"},"event":"x"}',
     '{"id":55,"snapshot":{"at":"home"},"actions":[]}', "", "v dropped on output"),
    ("s-v-2", '{"id":56,"op":"step","snapshot":{"v":2,"at":"home"},"event":"x"}',
     '{"id":56,"error":"снимок: неизвестная версия"}', "", ""),
    ("s-null", '{"id":57,"op":"step","snapshot":null,"event":"x"}',
     '{"id":57,"error":"снимок должен быть объектом"}', "", ""),
    ("s-missing", '{"id":58,"op":"step","event":"x"}', '{"id":58,"error":"нужно поле «snapshot»"}', "", ""),
    ("restart-seq-1e0", '{"id":59,"op":"restart","snapshot":{"at":"vip","seq":1e0,"resume":{"shop":"sub"}}}',
     '{"id":59,"snapshot":{"at":"home","timers":{"idle":2},"seq":2},"actions":[{"start_timer":"idle","ms":60000,"token":2},{"send":"home"}]}',
     "", "resume cleared"),
    # ---- framing --------------------------------------------------------
    ("two-objects", '{"id":60,"op":"hello"}{"id":61,"op":"hello"}', '{"id":null,"error":"лишний текст после JSON"}',
     "ET-07", ""),
    ("crlf", '{"id":62,"op":"hello"}\r', '{"id":62,"protocol":1,"states":8}', "", "CR is JSON whitespace"),
    ("bom", '﻿{"id":63,"op":"hello"}', '{"id":null,"error":"неожиданный символ «﻿»"}', "", "BOM rejected"),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--break", dest="brk", default=None, help="flip | lax-id")
    ap.add_argument("--record", action="store_true", help="print actual replies only")
    a = ap.parse_args()
    data = "".join(line + "\n" for _, line, *_ in ROWS).encode("utf-8")
    rc, out, err = run([ENGINE, DOC], data)
    replies = out.decode("utf-8").split("\n")[:-1]
    if rc != 0 or len(replies) != len(ROWS):
        print(f"engine rc={rc}, replies {len(replies)} of {len(ROWS)}: {err[:300]!r}")
        sys.exit(2)
    bad = 0
    tags = {}
    for (rid, line, exp, tag, why), got in zip(ROWS, replies):
        if a.brk == "flip" and rid == "g-true":
            exp = exp.replace("vip", "shop")
        if a.brk == "lax-id":
            # a broken oracle that believes the id is echoed byte-for-byte
            try:
                req_id = line[line.index('"id":') + 5:].split(',"op"')[0] if line.startswith('{"id":') else None
            except ValueError:
                req_id = None
            if req_id is not None and exp.startswith('{"id":') and '"id":null' not in exp:
                exp = '{"id":' + req_id + exp[exp.index(","):]
        ok = got == exp
        # every reply must be one valid JSON object (sanity, independent of the table)
        try:
            obj = json.loads(got)
            ok = ok and isinstance(obj, dict) and "id" in obj
        except ValueError:
            ok = False
        bad += not ok
        tags[tag or "-"] = tags.get(tag or "-", 0) + 1
        if a.record:
            print(f"{rid}\t{got}")
        else:
            print(f"{'    ' if ok else 'DIFF '}{rid:22s} {tag:6s} {got[:150]}" + ("" if ok else f"\n      expected {exp[:150]}"))
    print(f"requests={len(ROWS)} replies={len(replies)} mismatches={bad} tags={tags} break={a.brk}")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()

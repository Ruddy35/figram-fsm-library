#!/usr/bin/env python3
"""(c) JSON Schema vs decoder/check, handwritten boundary table.

Documents: figram.schema.json (jsonschema Draft 2020-12 on Python json.loads —
what an editor/host typically does) vs `figram fmt` (= parse + decode) vs
`figram check` (decode + V1–V5 + structural checks).
Snapshots: snapshot.schema.json vs figram-engine `step` (decSnapshot).

Every row carries the handwritten expected verdicts and a classification:
  agree      schema, decoder and check give the same answer;
  V          schema accepts, check rejects: semantic condition V1–V5 that the
             schema cannot express (docs/format.md, section 3, "What remains in V", and section 6);
  struct     schema rejects, decoder accepts, check rejects: the schema is
             stricter than the decoder but check catches it (empty names,
             empty children, 2^53 delays, keys without x-) — consistent at the
             check level;
  dev        documented text-layer deviation (duplicate keys, lone
             surrogates, |exponent| > 1000, depth > 256): Python accepts;
  prec       schema validator works on binary floats (Python/ajv), decoder is
             exact: schema accepts a non-integer / non-1 value  — tool artefact;
  footgun    agreement, but the result is surprising (see note).

    python3 c_schema_table.py [--break no-minlength|const2|no-integer]
"""

import argparse
import copy
import json
import os
import sys

import jsonschema

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import ENGINE, FIGRAM, ROOT, run  # noqa: E402


def D(bot_children='{"a":{}}', top="", sec="", initial='"a"'):
    return '{"figram":1' + top + ',"bot":{"initial":' + initial + sec + ',"children":' + bot_children + '}}'


def M(msg):
    """Document whose single message 'a' is `msg` (a JSON object text)."""
    return D('{"a":' + msg + '}')


V_A = '{"on":{"btn:x":[VAR]}}'


def VAR(v):
    return M(V_A.replace("VAR", v))


DOCS = [
    # id, text, schema, decode, check, class, note
    ("base", D(), 1, 1, 1, "agree", ""),
    ("figram-1.0", D().replace('"figram":1', '"figram":1.0'), 1, 1, 1, "agree", "decision 12: by value"),
    ("figram-1e0", D().replace('"figram":1', '"figram":1e0'), 1, 1, 1, "agree", ""),
    ("figram-10e-1", D().replace('"figram":1', '"figram":10e-1'), 1, 1, 1, "agree", ""),
    ("figram-1+1e-16", D().replace('"figram":1', '"figram":1.0000000000000001'), 1, 0, 0, "prec",
     "float(…)=1.0 == const 1"),
    ("figram-true", D().replace('"figram":1', '"figram":true'), 0, 0, 0, "agree", ""),
    ("figram-str", D().replace('"figram":1', '"figram":"1"'), 0, 0, 0, "agree", ""),
    ("figram-2", D().replace('"figram":1', '"figram":2'), 0, 0, 0, "agree", ""),
    ("children-empty", D('{}'), 0, 1, 0, "struct", "minProperties 1 vs V2 (initial not a child)"),
    ("initial-not-child", D(initial='"b"'), 1, 1, 0, "V", "V2"),
    ("initial-grandchild", D('{"s":{"initial":"a","children":{"a":{}}}}', initial='"a"'), 1, 1, 0, "V",
     "V2: direct child only"),
    ("initial-empty", D(initial='""'), 0, 1, 0, "struct", ""),
    ("child-name-empty", D('{"a":{},"":{}}'), 0, 1, 0, "struct", ""),
    ("event-name-empty", M('{"on":{"":[{"to":"a"}]}}'), 0, 1, 0, "struct", ""),
    ("timer-name-empty", M('{"timers":{"":5}}'), 0, 1, 0, "struct", ""),
    ("cond-name-empty", D(top=',"conditions":{"":"x"}'), 0, 1, 0, "struct", ""),
    ("to-empty", VAR('{"to":""}'), 0, 1, 0, "struct", ""),
    ("if-empty", VAR('{"if":"","to":"a"}'), 0, 1, 0, "struct", ""),
    ("do-empty-name", VAR('{"to":"a","do":[""]}'), 0, 1, 0, "struct", ""),
    ("x-keys-everywhere", '{"figram":1,"x-ui":{"z":1.5},"bot":{"initial":"a","x-ui":[],"children":'
     '{"a":{"x-content":{"text":"hi"},"on":{"btn:x":[{"to":"a","x-note":"n"}]}}}}}', 1, 1, 1, "agree", ""),
    ("non-x-unknown-keys", '{"figram":1,"foo":1,"bot":{"initial":"a","Bar":null,"children":'
     '{"a":{"zz":[],"on":{"btn:x":[{"to":"a","note":"n"}]}}}}}', 0, 1, 0, "struct",
     "kept by the decoder (C1), an error of check: extra keys start with x- (section 5)"),
    ("misspelt-if", VAR('{"iff":"c","to":"a"}'), 0, 1, 0, "struct", "a typo is not silently ignored"),
    ("misspelt-timers", M('{"tiemrs":{"t":5}}'), 0, 1, 0, "struct", "a typo is not silently ignored"),
    ("future-key-unless", VAR('{"unless":"c","to":"a"}'), 0, 1, 0, "struct",
     "looks like a guard: an error, not ignored (a new field comes with requires, C5b)"),
    ("x-ui-in-children", D('{"a":{},"x-ui":{"pos":[1,2]}}'), 0, 1, 0, "struct",
     "annotation key inside children = a new message node 'x-ui'; here its key `pos` is an error"),
    ("x-ui-in-children-empty", D('{"a":{},"x-ui":{}}'), 1, 1, 1, "footgun",
     "annotation key inside children = a new message node 'x-ui' (docs/format.md, section 5, says so)"),
    ("timer-7.2e6", M('{"timers":{"t":7.2e6}}'), 1, 1, 1, "agree", "decision 12"),
    ("timer-7200000.0", M('{"timers":{"t":7200000.0}}'), 1, 1, 1, "agree", ""),
    ("timer-72e5", M('{"timers":{"t":72e5}}'), 1, 1, 1, "agree", ""),
    ("timer-720000000e-2", M('{"timers":{"t":720000000e-2}}'), 1, 1, 1, "agree", ""),
    ("timer-neg-zero", M('{"timers":{"t":-0}}'), 1, 1, 1, "agree", ""),
    ("timer-neg-zero-frac", M('{"timers":{"t":-0.0}}'), 1, 1, 1, "agree", ""),
    ("timer-0.5", M('{"timers":{"t":0.5}}'), 0, 0, 0, "agree", ""),
    ("timer-neg", M('{"timers":{"t":-1}}'), 0, 0, 0, "agree", ""),
    ("timer-max", M('{"timers":{"t":9007199254740991}}'), 1, 1, 1, "agree", ""),
    ("timer-max.0", M('{"timers":{"t":9007199254740991.0}}'), 1, 1, 1, "agree", ""),
    ("timer-max+1", M('{"timers":{"t":9007199254740992}}'), 0, 1, 0, "struct",
     "fmt accepts/prints it, check rejects"),
    ("timer-9e18", M('{"timers":{"t":9e18}}'), 0, 1, 0, "struct", ""),
    ("timer-1e19", M('{"timers":{"t":1e19}}'), 0, 0, 0, "agree", "decoder shortcut e>18"),
    ("timer-max-1-half", M('{"timers":{"t":9007199254740990.5}}'), 1, 0, 0, "prec",
     "float rounds to …990"),
    ("timer-1e-400", M('{"timers":{"t":1e-400}}'), 1, 0, 0, "prec", "float underflow to 0.0"),
    ("timer-1e400", M('{"timers":{"t":1e400}}'), 0, 0, 0, "agree", "Python: inf, not integer"),
    ("timer-str", M('{"timers":{"t":"5"}}'), 0, 0, 0, "agree", ""),
    ("timer-bool", M('{"timers":{"t":true}}'), 0, 0, 0, "agree", ""),
    ("timer-null", M('{"timers":{"t":null}}'), 0, 0, 0, "agree", ""),
    ("timers-empty-on-msg", M('{"timers":{}}'), 1, 1, 1, "agree", "explicit default"),
    ("timers-on-section", D(sec=',"timers":{}'), 0, 0, 0, "agree", ""),
    ("initial-on-message", M('{"initial":"a"}'), 0, 0, 0, "agree", ""),
    ("resume-false", VAR('{"to":"a","resume":false}'), 1, 1, 1, "agree", "explicit default"),
    ("resume-str", VAR('{"to":"a","resume":"true"}'), 0, 0, 0, "agree", ""),
    ("resume-null", VAR('{"to":"a","resume":null}'), 0, 0, 0, "agree", ""),
    ("resume-to-message", VAR('{"to":"a","resume":true}'), 1, 1, 0, "V", "V3"),
    ("back-true", VAR('{"to":"a","back":true}'), 1, 1, 1, "agree", ""),
    ("back-false", VAR('{"to":"a","back":false}'), 1, 1, 1, "agree", "explicit default"),
    ("back-str", VAR('{"to":"a","back":"true"}'), 0, 0, 0, "agree", ""),
    ("back-null", VAR('{"to":"a","back":null}'), 0, 0, 0, "agree", ""),
    ("back-no-to", VAR('{"back":true}'), 0, 0, 0, "agree", "the fallback `to` is required"),
    ("back-to-missing", VAR('{"to":"nope","back":true}'), 1, 1, 0, "V", "V3: the fallback exists"),
    ("back-resume-message", VAR('{"to":"a","resume":true,"back":true}'), 1, 1, 0, "V", "V3"),
    ("do-empty", VAR('{"to":"a","do":[]}'), 1, 1, 1, "agree", "explicit default"),
    ("do-str", VAR('{"to":"a","do":"k"}'), 0, 0, 0, "agree", ""),
    ("requires-empty", D(top=',"requires":[]'), 1, 1, 1, "agree", ""),
    ("requires-unknown", D(top=',"requires":["x"]'), 0, 0, 0, "agree", "C5b"),
    ("requires-dup", D(top=',"requires":["x","x"]'), 0, 0, 0, "agree", ""),
    ("requires-str", D(top=',"requires":"x"'), 0, 0, 0, "agree", ""),
    ("dup-node-across-sections", D('{"a":{},"s":{"initial":"a","children":{"a":{}}}}'), 1, 1, 0, "V", "V1"),
    ("undeclared-cond", VAR('{"if":"c","to":"a"}'), 1, 1, 0, "V", "V4"),
    ("undeclared-call", VAR('{"to":"a","do":["k"]}'), 1, 1, 0, "V", "V4"),
    ("timer-event-no-timer", M('{"on":{"timer:t":[{"to":"a"}]}}'), 1, 1, 0, "V", "V5"),
    ("timer-event-empty", M('{"on":{"timer:":[{"to":"a"}]}}'), 1, 1, 0, "V", "V5"),
    ("timer-event-other-msg", D('{"a":{"on":{"timer:t":[{"to":"b"}]}},"b":{"timers":{"t":5}}}'), 1, 1, 1,
     "footgun", "V5 is global: handler for another message's timer is accepted"),
    ("to-missing", VAR('{"to":"nope"}'), 1, 1, 0, "V", "V3"),
    ("bot-is-message", '{"figram":1,"bot":{}}', 0, 0, 0, "agree", ""),
    ("node-array", D('{"a":[]}'), 0, 0, 0, "agree", ""),
    ("variant-no-to", VAR('{"if":"c"}'), 0, 0, 0, "agree", ""),
    ("on-value-obj", M('{"on":{"btn:x":{}}}'), 0, 0, 0, "agree", ""),
    ("on-array", M('{"on":[]}'), 0, 0, 0, "agree", ""),
    ("on-null", M('{"on":null}'), 0, 0, 0, "agree", ""),
    ("children-null", D('null'), 0, 0, 0, "agree", ""),
    ("variables", D(top=',"variables":{"v":"x"}'), 1, 1, 1, "agree", ""),
    ("cond-desc-num", D(top=',"conditions":{"c":1}'), 0, 0, 0, "agree", ""),
    ("dup-bot-key", '{"figram":1,"bot":{"initial":"z","children":{"z":{}}},"bot":{"initial":"a","children":{"a":{}}}}',
     1, 0, 0, "dev", "Python: last wins"),
    ("dup-key-in-annotation", '{"figram":1,"x-ui":{"k":1,"k":2},"bot":{"initial":"a","children":{"a":{}}}}',
     1, 0, 0, "dev", ""),
    ("lone-surrogate-name", D('{"a":{},"\\ud800":{}}'), 1, 0, 0, "dev", ""),
    ("annotation-exp-1001", '{"figram":1,"x-ui":1e1001,"bot":{"initial":"a","children":{"a":{}}}}', 1, 0, 0,
     "dev", "Python: inf"),
    ("annotation-depth-300", '{"figram":1,"x-ui":' + "[" * 300 + "]" * 300 + ',"bot":{"initial":"a","children":{"a":{}}}}',
     1, 0, 0, "dev", ""),
]

SNAP_DOC = os.path.join(ROOT, "examples", "features.bot.json")
SNAPS = [
    # id, snapshot text, schema, engine, class, note
    ("at", '{"at":"home"}', 1, 1, "agree", ""),
    ("seq-1.0", '{"at":"home","seq":1.0}', 1, 1, "agree", ""),
    ("seq-1e0", '{"at":"home","seq":1e0}', 1, 1, "agree", ""),
    ("seq-10e-1", '{"at":"home","seq":10e-1}', 1, 1, "agree", ""),
    ("seq-neg-zero", '{"at":"home","seq":-0}', 1, 1, "agree", ""),
    ("seq-neg-zero-frac", '{"at":"home","seq":-0.0}', 1, 1, "agree", ""),
    ("seq-1.5", '{"at":"home","seq":1.5}', 0, 0, "agree", ""),
    ("seq-max.0", '{"at":"home","seq":9007199254740991.0}', 1, 1, "agree", ""),
    ("seq-max-1-half", '{"at":"home","seq":9007199254740990.5}', 1, 0, "prec", ""),
    ("seq-1e-400", '{"at":"home","seq":1e-400}', 1, 0, "prec", ""),
    ("seq-bool", '{"at":"home","seq":true}', 0, 0, "agree", ""),
    ("seq-null", '{"at":"home","seq":null}', 0, 0, "agree", ""),
    ("v-1", '{"v":1,"at":"home"}', 1, 1, "agree", ""),
    ("v-1.0", '{"v":1.0,"at":"home"}', 1, 1, "agree", ""),
    ("v-1e0", '{"v":1e0,"at":"home"}', 1, 1, "agree", ""),
    ("v-true", '{"v":true,"at":"home"}', 0, 0, "agree", ""),
    ("v-0", '{"v":0,"at":"home"}', 0, 0, "agree", ""),
    ("v-neg-zero", '{"v":-0,"at":"home"}', 0, 0, "agree", ""),
    ("v-null", '{"v":null,"at":"home"}', 0, 0, "agree", ""),
    ("v-str", '{"v":"1","at":"home"}', 0, 0, "agree", ""),
    ("v-1+1e-16", '{"v":1.0000000000000001,"at":"home"}', 1, 0, "prec", ""),
    ("token-1.0", '{"at":"home","timers":{"idle":1.0}}', 1, 1, "agree", ""),
    ("resume-unknown-sec", '{"at":"home","resume":{"nope":"nope"}}', 1, 1, "agree", "kept as is"),
    ("back", '{"at":"home","back":"catalog"}', 1, 1, "agree", ""),
    ("back-unknown", '{"at":"home","back":"nope"}', 1, 1, "agree", "kept; \"back\" falls back to `to`"),
    ("back-empty", '{"at":"home","back":""}', 0, 0, "agree", ""),
    ("back-num", '{"at":"home","back":1}', 0, 0, "agree", ""),
    ("back-null", '{"at":"home","back":null}', 0, 0, "agree", ""),
    ("at-unknown", '{"at":"nope"}', 1, 1, "agree", "recovered"),
    ("dup-at", '{"at":"home","at":"vip"}', 1, 0, "dev", "Python last wins; engine: parse error id:null (ET-07)"),
    ("at-lone-surrogate", '{"at":"\\ud800"}', 1, 0, "dev", ""),
    ("unknown-field", '{"at":"home","x-ui":1}', 0, 0, "agree", "strict snapshot"),
]


def schema(path, brk):
    s = json.load(open(path, encoding="utf-8"))
    if brk == "no-minlength":
        def strip(o):
            if isinstance(o, dict):
                o.pop("minLength", None)
                o.pop("minProperties", None)
                for v in o.values():
                    strip(v)
            elif isinstance(o, list):
                for v in o:
                    strip(v)
        strip(s)
    elif brk == "const2" and "figram" in s.get("properties", {}):
        s["properties"]["figram"]["const"] = 2
    elif brk == "no-integer":
        s = json.loads(json.dumps(s).replace('"integer"', '"number"'))
    return jsonschema.Draft202012Validator(s)


def schema_ok(v, text):
    try:
        inst = json.loads(text)
    except ValueError:
        return 0
    return int(v.is_valid(inst))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--break", dest="brk", default=None)
    ap.add_argument("--tmp", default=os.environ.get("TMPDIR", "/tmp"))
    a = ap.parse_args()
    dv = schema(os.path.join(ROOT, "schemas", "figram.schema.json"), a.brk)
    sv = schema(os.path.join(ROOT, "schemas", "snapshot.schema.json"), a.brk)
    tmp = os.path.join(a.tmp, f"fc-c-{os.getpid()}.json")
    bad = 0
    counts = {}
    print(f"{'id':28s} schema decode check | expected | class    note")
    for rid, text, es, ed, ec, cls, note in DOCS:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
        s = schema_ok(dv, text)
        rc_f, out_f, err_f = run([FIGRAM, "fmt", tmp])
        rc_c, out_c, err_c = run([FIGRAM, "check", tmp])
        d = int(rc_f == 0)
        c = int(rc_c == 0 and out_c == b"ok\n")
        ok = (s, d, c) == (es, ed, ec)
        bad += not ok
        counts[cls] = counts.get(cls, 0) + 1
        why = (err_f.decode().strip() or out_c.decode().strip()).replace("\n", " | ")[:90]
        print(f"{'' if ok else 'DIFF '}{rid:28s} {s:6d} {d:6d} {c:5d} | {es}{ed}{ec}      | {cls:8s} {note}"
              + (f"  [{why}]" if not c else ""))
        if rid == "x-ui-in-children-empty":
            print("    stats:", run([FIGRAM, "stats", tmp])[1].decode().strip(),
                  "(base has states=1)")
    # snapshots
    lines = []
    for i, (rid, snap, *_rest) in enumerate(SNAPS):
        lines.append('{"id":%d,"op":"step","event":"unknown:event","snapshot":%s}' % (i, snap))
    rc, out, err = run([ENGINE, SNAP_DOC], ("\n".join(lines) + "\n").encode())
    replies = out.decode().split("\n")[:-1]
    if rc != 0 or len(replies) != len(SNAPS):
        print("engine failed", rc, len(replies), err[:200])
        sys.exit(2)
    print(f"\n{'snapshot id':28s} schema engine | expected | class   reply")
    for (rid, snap, es, ee, cls, note), rep in zip(SNAPS, replies):
        s = schema_ok(sv, snap)
        e = int('"snapshot"' in rep and '"error"' not in rep)
        ok = (s, e) == (es, ee)
        bad += not ok
        counts["snap-" + cls] = counts.get("snap-" + cls, 0) + 1
        print(f"{'' if ok else 'DIFF '}{rid:28s} {s:6d} {e:6d} | {es}{ee}       | {cls:7s} {rep[:110]}  {note}")
    total = len(DOCS) + len(SNAPS)
    print(f"\nrows={total} (docs {len(DOCS)}, snapshots {len(SNAPS)}) classes={counts} "
          f"mismatches-vs-expected={bad} break={a.brk}")
    os.unlink(tmp)
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()

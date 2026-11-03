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

    python3 c_schema_table.py [--break no-minlength|const2|no-integer|to-required|no-pending|lax-call]
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


def CALL(c, on='{"btn:x":[{"do":["k"]}]}'):
    """Document with the action `k` declared as `c` (a JSON text) and used by message 'a'."""
    return D('{"a":{"on":' + on + '}}', top=',"calls":{"k":' + c + '}')


# The action `k` with outcomes, used by message 'a'; `on` holds its outcome arrows.
K_OUT = '{"text":"t","outcomes":["ok"]}'
OUT_ON = '{"btn:x":[{"do":["k"]}],"call:k:ok":[{}],"call:k:failed":[{}]'


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
    ("back-no-to", VAR('{"back":true}'), 0, 1, 0, "struct", "the fallback `to` is required (V3)"),
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
    ("variant-no-to-undeclared-if", VAR('{"if":"c"}'), 1, 1, 0, "V", "V4; no `to` is no move (decision 13)"),
    ("variant-empty", VAR('{}'), 1, 1, 1, "agree", "no move, no actions"),
    ("variant-no-to-do", CALL('"t"', '{"btn:x":[{"do":["k"]}]}'), 1, 1, 1, "agree", "actions without a move"),
    ("variant-no-to-back-false", VAR('{"back":false}'), 1, 1, 1, "agree", "explicit default"),
    ("variant-no-to-resume", VAR('{"resume":true}'), 0, 1, 0, "struct", "`resume` needs `to` (V3)"),
    ("variant-to-null", VAR('{"to":null}'), 0, 0, 0, "agree", ""),
    ("variant-to-num", VAR('{"to":1}'), 0, 0, 0, "agree", ""),
    # ---- actions: a text or {text, detached?, outcomes?, every?, x-…} (section 4, `Call`) ----
    ("call-text", CALL('"t"'), 1, 1, 1, "agree", ""),
    ("call-object-text", CALL('{"text":"t"}'), 1, 1, 1, "agree", "the same action as the text"),
    ("call-text-empty", CALL('""'), 1, 1, 1, "agree", "a description may be empty"),
    ("call-detached", CALL('{"text":"t","detached":true}'), 1, 1, 1, "agree", "decision 11"),
    ("call-detached-false", CALL('{"text":"t","detached":false}'), 1, 1, 1, "agree", "explicit default"),
    ("call-detached-str", CALL('{"text":"t","detached":"true"}'), 0, 0, 0, "agree", ""),
    ("call-detached-null", CALL('{"text":"t","detached":null}'), 0, 0, 0, "agree", ""),
    ("call-outcomes", CALL(K_OUT, OUT_ON + '}'), 1, 1, 1, "agree", "decision 12"),
    ("call-outcomes-two", CALL('{"text":"t","outcomes":["ok","no"]}'), 1, 1, 1, "agree",
     "outcomes without arrows: a warning, not an error"),
    ("call-outcomes-empty", CALL('{"text":"t","outcomes":[]}'), 1, 1, 1, "agree", "explicit default"),
    ("call-outcomes-str", CALL('{"text":"t","outcomes":"ok"}'), 0, 0, 0, "agree", ""),
    ("call-outcomes-num", CALL('{"text":"t","outcomes":[1]}'), 0, 0, 0, "agree", ""),
    ("call-outcome-empty", CALL('{"text":"t","outcomes":[""]}'), 0, 1, 0, "struct", "outcome names are non-empty"),
    ("call-outcome-colon", CALL('{"text":"t","outcomes":["a:b"]}'), 0, 1, 0, "struct",
     "an outcome name has no `:` (the event is split at the last `:`)"),
    ("call-outcome-failed", CALL('{"text":"t","outcomes":["failed"]}'), 0, 1, 0, "struct",
     "`failed` is implicit"),
    ("call-outcome-dup", CALL('{"text":"t","outcomes":["ok","ok"]}'), 0, 1, 0, "struct", "unique outcomes"),
    ("call-every", CALL('{"text":"t","outcomes":["ok"],"every":true}'), 1, 1, 1, "agree", ""),
    ("call-every-false", CALL('{"text":"t","every":false}'), 1, 1, 1, "agree", "explicit default"),
    ("call-every-no-outcomes", CALL('{"text":"t","every":true}'), 0, 1, 0, "struct", "`every` needs `outcomes`"),
    ("call-every-outcomes-empty", CALL('{"text":"t","outcomes":[],"every":true}'), 0, 1, 0, "struct", ""),
    ("call-every-str", CALL('{"text":"t","outcomes":["ok"],"every":1}'), 0, 0, 0, "agree", ""),
    ("call-all", CALL('{"x-ui":{"z":1},"every":true,"outcomes":["ok"],"detached":true,"text":"t"}', OUT_ON + '}'),
     1, 1, 1, "agree", "any key order, an annotation"),
    ("call-no-text", CALL('{"detached":true}'), 0, 0, 0, "agree", ""),
    ("call-text-num", CALL('{"text":1}'), 0, 0, 0, "agree", ""),
    ("call-num", CALL('5'), 0, 0, 0, "agree", ""),
    ("call-null", CALL('null'), 0, 0, 0, "agree", ""),
    ("call-array", CALL('["t"]'), 0, 0, 0, "agree", ""),
    ("call-unknown-key", CALL('{"text":"t","detach":true}'), 0, 1, 0, "struct",
     "a typo of `detached` is an error, not a sequential action"),
    ("call-name-empty", D(top=',"calls":{"":{"text":"t"}}'), 0, 1, 0, "struct", ""),
    # ---- outcome events `call:<action>:<outcome>` (V5) and V6 ----------------
    ("outcome-event-failed", CALL(K_OUT, OUT_ON + '}'), 1, 1, 1, "agree", "`failed` of every action with outcomes"),
    ("outcome-event-undeclared-outcome", CALL(K_OUT, OUT_ON + ',"call:k:nope":[{}]}'), 1, 1, 0, "V", "V5"),
    ("outcome-event-no-outcomes", CALL('"t"', '{"btn:x":[{"do":["k"]}],"call:k:ok":[{}]}'), 1, 1, 0, "V",
     "V5: `k` has no outcomes"),
    ("outcome-event-undeclared-action", M('{"on":{"call:zz:ok":[{}]}}'), 1, 1, 0, "V", "V5"),
    ("outcome-event-no-outcome", CALL(K_OUT, OUT_ON + ',"call:k":[{}]}'), 1, 1, 0, "V",
     "V5: `call:` is reserved"),
    ("outcome-event-empty", M('{"on":{"call:":[{}]}}'), 1, 1, 0, "V", "V5"),
    ("outcome-event-colon-action", D('{"a":{"on":{"btn:x":[{"do":["a:b"]}],"call:a:b:ok":[{}],"call:a:b:failed":[{}]}}}',
                                     top=',"calls":{"a:b":{"text":"t","outcomes":["ok"]}}'), 1, 1, 1, "agree",
     "split at the last `:`"),
    ("outcome-twice-in-do", CALL(K_OUT, OUT_ON.replace('"do":["k"]', '"do":["k","k"]') + '}'), 1, 1, 0, "V", "V6"),
    ("plain-twice-in-do", CALL('"t"', '{"btn:x":[{"do":["k","k"]}]}'), 1, 1, 1, "agree",
     "V6 is only for actions with outcomes"),
    ("outcome-no-arrow-on-target", D('{"a":{"on":{"btn:x":[{"to":"b","do":["k"]}]}},"b":{}}',
                                     top=',"calls":{"k":' + K_OUT + '}'), 1, 1, 1, "footgun",
     "a warning: `b` has no arrow for the outcomes, a user there would wait forever"),
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
    # ---- pending: waits for outcomes (section 8, protocol 3) -----------------
    ("pending", '{"at":"home","pending":[{"call":"ping","token":3}]}', 1, 1, "agree", ""),
    ("pending-two", '{"at":"home","pending":[{"call":"ping","token":5},{"call":"check_stock","token":3}]}', 1, 1,
     "agree", ""),
    ("pending-key-order", '{"at":"home","pending":[{"token":3,"call":"ping"}]}', 1, 1, "agree", ""),
    ("pending-empty", '{"at":"home","pending":[]}', 1, 1, "agree", "explicit default"),
    ("pending-dup", '{"at":"home","pending":[{"call":"ping","token":3},{"call":"ping","token":3}]}', 1, 1,
     "agree", "not produced by the engine, accepted"),
    ("pending-unknown-action", '{"at":"home","pending":[{"call":"nope","token":3}]}', 1, 1, "agree",
     "kept: still accepts its outcome"),
    ("pending-plain-action", '{"at":"home","pending":[{"call":"log","token":3}]}', 1, 1, "agree", "kept"),
    ("pending-token-1.0", '{"at":"home","pending":[{"call":"ping","token":1.0}]}', 1, 1, "agree", "by value"),
    ("pending-token-neg-zero", '{"at":"home","pending":[{"call":"ping","token":-0}]}', 1, 1, "agree", ""),
    ("pending-token-max", '{"at":"home","pending":[{"call":"ping","token":9007199254740991}]}', 1, 1, "agree", ""),
    ("pending-token-max+1", '{"at":"home","pending":[{"call":"ping","token":9007199254740992}]}', 0, 0, "agree", ""),
    ("pending-token-neg", '{"at":"home","pending":[{"call":"ping","token":-1}]}', 0, 0, "agree", ""),
    ("pending-token-1.5", '{"at":"home","pending":[{"call":"ping","token":1.5}]}', 0, 0, "agree", ""),
    ("pending-token-1e-400", '{"at":"home","pending":[{"call":"ping","token":1e-400}]}', 1, 0, "prec", ""),
    ("pending-token-max-1-half", '{"at":"home","pending":[{"call":"ping","token":9007199254740990.5}]}', 1, 0,
     "prec", ""),
    ("pending-token-str", '{"at":"home","pending":[{"call":"ping","token":"3"}]}', 0, 0, "agree", ""),
    ("pending-token-bool", '{"at":"home","pending":[{"call":"ping","token":true}]}', 0, 0, "agree", ""),
    ("pending-token-null", '{"at":"home","pending":[{"call":"ping","token":null}]}', 0, 0, "agree", ""),
    ("pending-call-empty", '{"at":"home","pending":[{"call":"","token":3}]}', 0, 0, "agree", ""),
    ("pending-call-num", '{"at":"home","pending":[{"call":1,"token":3}]}', 0, 0, "agree", ""),
    ("pending-no-token", '{"at":"home","pending":[{"call":"ping"}]}', 0, 0, "agree", ""),
    ("pending-no-call", '{"at":"home","pending":[{"token":3}]}', 0, 0, "agree", ""),
    ("pending-extra-field", '{"at":"home","pending":[{"call":"ping","token":3,"outcome":"pong"}]}', 0, 0,
     "agree", "strict entries"),
    ("pending-x-key", '{"at":"home","pending":[{"call":"ping","token":3,"x-ui":1}]}', 0, 0, "agree", ""),
    ("pending-entry-array", '{"at":"home","pending":[["ping",3]]}', 0, 0, "agree", ""),
    ("pending-entry-str", '{"at":"home","pending":["ping"]}', 0, 0, "agree", ""),
    ("pending-object", '{"at":"home","pending":{"ping":3}}', 0, 0, "agree", "an array, not a map"),
    ("pending-null", '{"at":"home","pending":null}', 0, 0, "agree", ""),
    ("pending-dup-key", '{"at":"home","pending":[{"call":"ping","call":"log","token":3}]}', 1, 0, "dev",
     "Python last wins; engine: parse error"),
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
    elif brk == "to-required" and "variant" in s.get("$defs", {}):
        # the protocol-2 schema: every variant has a target, actions are texts
        s["$defs"]["variant"]["required"] = ["to"]
        s["$defs"]["call"] = {"type": "string"}
    elif brk == "no-pending" and "pending" in s.get("properties", {}):
        # the protocol-2 snapshot schema: no waits
        del s["properties"]["pending"]
    elif brk == "lax-call" and "call" in s.get("$defs", {}):
        # an action object without its constraints: any keys, any outcome names, `every` alone
        s["$defs"]["call"]["anyOf"][1] = {"type": "object", "required": ["text"]}
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

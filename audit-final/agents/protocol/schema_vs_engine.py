"""Snapshots: schemas/snapshot.schema.json (jsonschema, Draft 2020-12) vs the engine's decoder
(`restart` with the snapshot: accepted = a result or seq_overflow; rejected = bad_snapshot or
parse_error). The snapshot text is spliced into the request verbatim (numbers as written).

usage: schema_vs_engine.py REPO DOC
"""
import json, os, subprocess, sys
import jsonschema

repo, doc = sys.argv[1], sys.argv[2]
schema = json.load(open(os.path.join(repo, "schemas", "snapshot.schema.json")))
V = jsonschema.Draft202012Validator(schema)
eng = subprocess.Popen([os.path.join(repo, ".lake/build/bin/figram-engine"), doc],
                       stdin=subprocess.PIPE, stdout=subprocess.PIPE)

cases = [
    '{"at":"a"}',
    '{"at":"a","pending":[]}',
    '{"at":"a","resume":{},"timers":{}}',
    '{"at":"a","pending":[{"call":"x","token":1},{"call":"x","token":1}]}',
    '{"at":"a","pending":[{"token":1,"call":"x"}]}',
    '{"at":"a","pending":[{"call":"x","token":5.0}]}',
    '{"at":"a","pending":[{"call":"x","token":5e0}]}',
    '{"at":"a","pending":[{"call":"x","token":1e-400}]}',
    '{"at":"a","pending":[{"call":"x","token":9007199254740990.5}]}',
    '{"at":"a","pending":[{"call":"x","token":9007199254740991}]}',
    '{"at":"a","pending":[{"call":"x","token":9007199254740992}]}',
    '{"at":"a","pending":[{"call":"x","token":-0}]}',
    '{"at":"a","pending":[{"call":"x","token":-1}]}',
    '{"at":"a","pending":[{"call":"x","token":0e1001}]}',
    '{"at":"a","seq":0e1001}',
    '{"at":"a","seq":1e1000}',
    '{"at":"a","seq":100e-2}',
    '{"at":"a","pending":[{"call":"x","token":"1"}]}',
    '{"at":"a","pending":[{"call":"","token":1}]}',
    '{"at":"a","pending":[{"call":1,"token":1}]}',
    '{"at":"a","pending":[{"call":"x","token":1,"x-note":1}]}',
    '{"at":"a","pending":[null]}',
    '{"at":"a","pending":null}',
    '{"at":"a","pending":{}}',
    '{"at":"a","v":1.0}',
    '{"at":"a","v":true}',
    '{"at":"a","v":1e0}',
    '{"at":"\\ud800"}',
    '{"at":"\\u0000"}',
    '{"at":"a","pending":[{"call":"\\udfff","token":1}]}',
    '{"at":"a","back":""}',
    '{"at":"a","resume":{"":"x"}}',
    '{"at":"a","timers":{"t":1.5}}',
    '{"at":"a","seq":9007199254740991}',
    '{"at":"a","seq":true}',
    '{"at":"a","x-note":1}',
    '{"at":"a","at":"b"}',
    '{"at":"a","pending":[{"call":"x","token":1,"token":2}]}',
]
n = 0
mism = []
for c in cases:
    n += 1
    try:
        inst = json.loads(c)
        sch = "valid" if V.is_valid(inst) else "invalid"
    except ValueError as e:
        sch = "json-error"
    line = '{"id":%d,"op":"restart","snapshot":%s}\n' % (n, c)
    eng.stdin.write(line.encode()); eng.stdin.flush()
    r = json.loads(eng.stdout.readline())
    if "snapshot" in r or r.get("code") == "seq_overflow":
        en = "accepted"
    else:
        en = "rejected:" + r.get("code", "?")
    agree = (sch == "valid") == (en == "accepted")
    flag = "" if agree else "   <-- DISAGREE"
    if not agree:
        mism.append(c)
    print(f"{c:70s} schema={sch:8s} engine={en}{flag}")
eng.stdin.close(); eng.wait(10)
print(f"{n} snapshots, {len(mism)} disagreements")

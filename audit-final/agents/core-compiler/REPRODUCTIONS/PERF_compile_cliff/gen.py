#!/usr/bin/env python3
"""python3 gen.py hist S M  -> S sections x M messages, one hub with a resume arrow into every section
   python3 gen.py flat N E  -> N messages with 3 arrows each, E global root commands"""
import json, sys
kind, a, b = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
if kind == "hist":
    ch, back = {}, {}
    for s in range(a):
        kids = {f"m{s}_{i}": {"on": {"btn:n": [{"to": f"m{s}_{(i + 1) % b}"}]}} for i in range(b)}
        ch[f"s{s}"] = {"initial": f"m{s}_0", "on": {"btn:out": [{"to": "hub"}]}, "children": kids}
        back[f"btn:go{s}"] = [{"to": f"s{s}", "resume": True}]
    ch["hub"] = {"on": back}
    print(json.dumps({"figram": 1, "bot": {"initial": "hub", "children": ch}}))
else:
    ch = {f"m{i}": {"on": {f"btn:{j}": [{"to": f"m{(i + j + 1) % a}"}] for j in range(3)}} for i in range(a)}
    print(json.dumps({"figram": 1, "bot": {"initial": "m0", "on": {f"cmd:{j}": [{"to": "m0"}] for j in range(b)},
                                         "children": ch}}))

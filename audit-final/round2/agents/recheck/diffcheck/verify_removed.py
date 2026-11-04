#!/usr/bin/env python3
"""Every warning that 598fec6 no longer prints (OldNewCheck.lean stdout, argv[1]) names a message
that does not own the timer of the arrow: such an arrow never fires there (format.md section 8,
rule 2: a timer event of a timer that does not belong to the current message is ignored)."""
import json, re, sys
lines = open(sys.argv[1], encoding="utf-8").read().split("\n")
def timers_of(doc):
    out = {}
    def w(name, o):
        if "children" in o:
            for k, v in o["children"].items(): w(k, v)
        else:
            out[name] = set(o.get("timers", {}))
    w("bot", doc["bot"])
    return out
n = bad = 0
for i, l in enumerate(lines):
    if ": DIFF warnings" not in l: continue
    path = l.split(": DIFF")[0]
    tm = timers_of(json.load(open(path, encoding="utf-8")))
    for M, T in re.findall(r"has no arrow on `([^`]*)`: the variant of `[^`]*` on `timer:([^`]*)`", lines[i + 2]):
        n += 1
        if T in tm.get(M, set()):
            bad += 1; print("REMOVED BUT OWNED", path, M, T)
print(f"removed warnings {n}, on a message that owns the timer {bad}")
sys.exit(1 if bad or n == 0 else 0)

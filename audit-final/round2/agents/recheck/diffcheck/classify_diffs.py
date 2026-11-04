#!/usr/bin/env python3
"""Classify every difference printed by OldNewCheck.lean (its stdout file as argv[1]).
check: the only allowed difference is the shown name of a node with an empty key (`` vs `bot`)
in the V6 message, with the same acceptance. warnings: new must be a subset of old, and every
removed warning must be a variant on a `timer:` event without a target (checked further by
verify_removed.py against the document)."""
import re, sys
lines = open(sys.argv[1], encoding="utf-8").read().split("\n")
i = 0; kinds = {}; bad = []
while i < len(lines):
    l = lines[i]
    if ": DIFF check" in l:
        new, old = lines[i + 1], lines[i + 2]
        same_acc = re.search(r"accept new=(\w+) old=(\w+)", l)
        ok = same_acc.group(1) == same_acc.group(2) and new.replace("  new: ", "") == old.replace("  old: ", "").replace("`` → ", "`bot` → ")
        kinds["check: empty-key node shown as `bot`" if ok else "check: OTHER"] = kinds.get("check: empty-key node shown as `bot`" if ok else "check: OTHER", 0) + 1
        if not ok: bad.append(l)
        i += 3; continue
    if ": DIFF warnings" in l:
        newonly, oldonly = lines[i + 1], lines[i + 2]
        ok = newonly.strip() == "new-only: []" and all("on `timer:" in w for w in re.findall(r"the variant of `[^`]*` on `[^`]*`", oldonly))
        k = "warnings: removed only, all on timer: arrows" if ok else "warnings: OTHER"
        kinds[k] = kinds.get(k, 0) + 1
        if not ok: bad.append(l)
        i += 3; continue
    i += 1
print(kinds)
for b in bad[:10]: print("UNEXPLAINED", b)
sys.exit(1 if bad else 0)

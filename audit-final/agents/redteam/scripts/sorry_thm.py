#!/usr/bin/env python3
"""sorry_thm.py FILE THM [THM...]: replace the proof of each theorem with `sorry` (experiment only)."""
import re, sys
path, names = sys.argv[1], sys.argv[2:]
lines = open(path).read().split("\n")
for name in names:
    idx = next(i for i, l in enumerate(lines) if re.match(rf"^(@\[[^\]]*\]\s*)?(private\s+)?theorem {re.escape(name)}\b", l))
    # find the line with ':= by' (end of statement)
    j = idx
    while ":= by" not in lines[j]:
        j += 1
    k = j + 1
    while k < len(lines) and lines[k].startswith((" ", "\t")) and lines[k].strip():
        k += 1
    head = lines[j][: lines[j].index(":= by")] + ":= by sorry"
    lines[j:k] = [head]
    print(f"{name}: lines {j+1}-{k} -> sorry")
open(path, "w").write("\n".join(lines))

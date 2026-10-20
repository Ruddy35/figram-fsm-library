#!/usr/bin/env python3
"""`figram check` of two builds must print the same verdict and the same messages.

  python3 check_messages_diff.py --old FIGRAM_OLD --new FIGRAM_NEW [--docs N] [--fc07]

Documents: check_diff.py generator with every injected defect kind (valid ones
included) plus hand-made cases (FC-07 root names, duplicate ids, empty section).
Used for P-06: the linear lint must keep the messages of the quadratic one.
With --fc07 the expected difference of FC-07 is applied to the OLD output
first (root «» → «bot»; «Узел «X» объявлен несколько раз» once per id).
Warnings (stderr «предупреждение: …») are ignored: only stdout and exit code."""

import argparse
import json
import random
import re
import subprocess
import sys
import tempfile

from check_diff import KINDS, inject
from gen import dumps, gen_model, to_json

HAND = [
    '{"figram":1,"bot":{"initial":"b","children":{"a":{},"":{}}}}',
    '{"figram":1,"bot":{"initial":"a","children":{"a":{},"s":{"initial":"a","children":{"a":{}}}}}}',
    '{"figram":1,"bot":{"initial":"s","children":{"s":{"initial":"x","children":{}}}}}',
    '{"figram":1,"bot":{"initial":"a","on":{"btn:x":[{"to":"ghost"}]},"children":{"a":{"on":{"btn:y":[{"to":"s","resume":true}]}}}}}',
    '{"figram":1,"bot":{"initial":"a","children":{"a":{"on":{"btn:x":[{"to":"","resume":true}]}}}}}',
]


def check(figram, text):
    with tempfile.NamedTemporaryFile("w", suffix=".bot.json", delete=False) as f:
        f.write(text)
    p = subprocess.run([figram, "check", f.name], capture_output=True, text=True, timeout=120)
    return p.returncode, p.stdout


def fc07(out):
    out = out.replace("«»", "«bot»")
    lines, seen = [], set()
    for ln in out.splitlines():
        if re.match(r"^Узел «.*» объявлен несколько раз$", ln):
            if ln in seen:
                continue
            seen.add(ln)
        lines.append(ln)
    return "\n".join(lines) + ("\n" if out.endswith("\n") else "")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--old", required=True)
    ap.add_argument("--new", required=True)
    ap.add_argument("--docs", type=int, default=20)
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--fc07", action="store_true")
    a = ap.parse_args()
    texts = list(HAND)
    for k in range(a.docs):
        for kind in KINDS:
            rng = random.Random(a.seed * 7919 + k * 101 + KINDS.index(kind))
            d = to_json(gen_model(rng, n_msgs=rng.choice([4, 8]), n_secs=rng.choice([2, 3]),
                                  max_depth=3, p_hist=0.3, p_timer=0.4))
            if inject(rng, d, kind):
                texts.append(dumps(d))
    diffs = rejected = 0
    for t in texts:
        o, n = check(a.old, t), check(a.new, t)
        if o[0] != 0:
            rejected += 1
        exp = (o[0], fc07(o[1]) if a.fc07 else o[1])
        if exp != n:
            diffs += 1
            if diffs <= 5:
                print("DIFF", json.dumps({"doc": t[:300], "old": o, "new": n}, ensure_ascii=False))
    print(f"documents {len(texts)}, rejected by old {rejected}, differences {diffs}")
    sys.exit(0 if diffs == 0 and len(texts) > len(HAND) and rejected > 0 else 1)


if __name__ == "__main__":
    main()

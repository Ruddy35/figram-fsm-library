#!/usr/bin/env python3
"""Are the scenarios of `figram trace … --directed` valid reference scenarios? Each scenario line
is replayed with the independent oracle of tests/compiler (written from the prose, it does not
read the Lean sources): the start reply and every step (event, token, guards → snapshot, actions,
recovered) must equal the oracle's. The directed scenarios (the lines after the random ones) are
counted separately, including the outcome scenarios (stale, accepted, repeated).

  python3 directed_vs_oracle.py REPO FIGRAM [--docs N] [--seed S] [--break-oracle]

Documents: the two examples and N `figram random` documents. --break-oracle is the negative
control: the oracle with the bug `outcome-any-token` (accepts an outcome with any token) must
disagree with the stale-token steps."""
import json
import os
import subprocess
import sys
import tempfile

REPO, FIGRAM = sys.argv[1], sys.argv[2]
args = sys.argv[3:]
N = int(args[args.index("--docs") + 1]) if "--docs" in args else 30
SEED = int(args[args.index("--seed") + 1]) if "--seed" in args else 1
BREAK = "--break-oracle" in args
sys.path.insert(0, os.path.join(REPO, "tests", "compiler"))
from oracle import Bot, Options  # noqa: E402
from difflib_core import norm_snap  # noqa: E402

docs = [os.path.join(REPO, "examples", d) for d in ("pizza.bot.json", "features.bot.json")]
tmp = tempfile.mkdtemp()
for k in range(N):
    p = os.path.join(tmp, f"r{k}.bot.json")
    with open(p, "w") as f:
        f.write(subprocess.run([FIGRAM, "random", str(SEED + k), str(4 + k % 12)], capture_output=True, text=True,
                               check=True).stdout)
    docs.append(p)

opts = dict(timer_accept="owned", exit_cancels="snapshot")
total_steps = directed_steps = stale_outcomes = bad = scen_dir = 0
for d in docs:
    text = open(d, encoding="utf-8").read()
    bot = Bot(text, Options(bug="outcome-any-token" if BREAK else None, **opts))
    out = subprocess.run([FIGRAM, "trace", d, "3", "10", "5", "--directed"], capture_output=True, text=True,
                         check=True, timeout=600)
    lines = out.stdout.splitlines()
    for li, line in enumerate(lines):
        sc = json.loads(line)
        directed = li >= 3
        scen_dir += directed
        st = sc["start"]
        osnap, oacts = bot.start()
        if norm_snap(st["snapshot"]) != osnap or st["actions"] != oacts:
            bad += 1
            print("START DIFFERS", d, li, st, o)
            continue
        cur = osnap
        for si, step in enumerate(sc["steps"]):
            total_steps += 1
            directed_steps += directed
            ev, tok, gs = step["event"], step.get("token"), step["guards"]
            if ev.startswith("call:") and directed and tok is not None and not any(
                    w["call"] == ev[5:].rsplit(":", 1)[0] and w["token"] == tok for w in cur["pending"]):
                stale_outcomes += 1
            r = bot.step(cur, ev, tok, gs, check_need=False)
            _, s2, acts, rec = r
            if norm_snap(step["snapshot"]) != s2 or step["actions"] != acts or bool(step.get("recovered", False)) != rec:
                bad += 1
                print("STEP DIFFERS", os.path.basename(d), "line", li, "step", si, ev, tok,
                      json.dumps({"trace": [step["snapshot"], step["actions"]], "oracle": [s2, acts]})[:400])
                break
            cur = s2
print(f"documents {len(docs)}, directed scenarios {scen_dir}, steps {total_steps} (directed {directed_steps}, "
      f"stale outcomes in directed {stale_outcomes}), scenarios that differ from the oracle {bad}")
sys.exit(0 if bad == 0 and directed_steps > 0 and stale_outcomes > 0 else 1)

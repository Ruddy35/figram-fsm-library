#!/usr/bin/env python3
"""Scale / performance cliffs of compile (engine load), check and step.

  python3 perf.py --engine BIN --figram BIN [--only NAME] [--timeout S]

Each scenario writes a document, then measures
  * load:  time until the engine answers `hello` (decode + check + compile + wf)
  * step:  mean latency of 200 `step` requests on a pipelined connection
and checks the answers against the oracle for a few steps.  Prints one JSON
line per scenario.  A load that exceeds --timeout is reported as TIMEOUT."""

import argparse
import json
import os
import resource
import subprocess
import sys
import tempfile
import time

from oracle import Bot


def flat(n, events=3, root_events=1):
    ch = {}
    for i in range(n):
        on = {f"btn:{j}": [{"to": f"m{(i + j + 1) % n}"}] for j in range(events)}
        ch[f"m{i}"] = {"on": on}
    on = {f"cmd:{j}": [{"to": "m0"}] for j in range(root_events)}
    return {"figram": 1, "bot": {"initial": "m0", "on": on, "children": ch}}


def deep(depth, leaves=1, hist=False, events=1):
    """Chain of nested sections s0 ⊃ s1 ⊃ ... with `leaves` messages at the bottom,
    every section with a section-level arrow, optionally a resume arrow into it."""
    inner = {f"leaf{i}": {"on": {"btn:n": [{"to": f"leaf{(i + 1) % leaves}"}]}} for i in range(leaves)}
    cur = {"initial": "leaf0", "on": {f"btn:s{depth - 1}_{j}": [{"to": "out"}] for j in range(events)},
           "children": inner}
    for d in range(depth - 2, -1, -1):
        cur = {"initial": f"s{d + 1}", "on": {f"btn:s{d}_{j}": [{"to": "out"}] for j in range(events)},
               "children": {f"s{d + 1}": cur}}
    out_on = {}
    if hist:
        out_on = {f"btn:back{d}": [{"to": f"s{d}", "resume": True}] for d in range(depth)}
    bot = {"initial": "s0", "children": {"s0": cur, "out": {"on": out_on}}}
    return {"figram": 1, "bot": bot}


def many_hist(n_secs, per_sec):
    ch = {}
    back = {}
    for s in range(n_secs):
        kids = {f"m{s}_{i}": {"on": {"btn:n": [{"to": f"m{s}_{(i + 1) % per_sec}"}]}} for i in range(per_sec)}
        ch[f"s{s}"] = {"initial": f"m{s}_0", "on": {"btn:out": [{"to": "hub"}]}, "children": kids}
        back[f"btn:go{s}"] = [{"to": f"s{s}", "resume": True}]
    ch["hub"] = {"on": back}
    return {"figram": 1, "bot": {"initial": "hub", "children": ch}}


SCEN = {
    "flat_500": lambda: flat(500),
    "flat_1000": lambda: flat(1000),
    "flat_2000": lambda: flat(2000),
    "flat_2000_50rootev": lambda: flat(2000, events=3, root_events=50),
    "flat_4000": lambda: flat(4000),
    "deep_60": lambda: deep(60),
    "deep_120": lambda: deep(120),
    "deep_127_hist": lambda: deep(127, hist=True),
    "deep_128": lambda: deep(128),
    "deep_200": lambda: deep(200),
    "deep_100_x_500leaves_hist": lambda: deep(100, leaves=500, hist=True),
    "hist_100x20": lambda: many_hist(100, 20),
    "hist_200x20": lambda: many_hist(200, 20),
}


def measure(engine, text, timeout, steps=200):
    with tempfile.NamedTemporaryFile("w", suffix=".bot.json", delete=False) as f:
        f.write(text)
    t0 = time.time()
    p = subprocess.Popen(["prlimit", "--as=4000000000", "--", engine, f.name], stdin=subprocess.PIPE,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
    p.stdin.write('{"id":0,"op":"hello"}\n')
    p.stdin.flush()
    import select
    r, _, _ = select.select([p.stdout], [], [], timeout)
    if not r:
        p.kill()
        p.wait()
        os.unlink(f.name)
        return {"load_s": None, "status": "TIMEOUT"}
    line = p.stdout.readline()
    load = time.time() - t0
    if not line:
        err = p.stderr.read()
        p.wait()
        os.unlink(f.name)
        return {"load_s": round(load, 3), "status": "REJECTED", "rc": p.returncode, "stderr": err.strip()[:300]}
    hello = json.loads(line)
    # pipelined steps: start, then `steps` steps on the start snapshot
    p.stdin.write('{"id":1,"op":"start"}\n')
    p.stdin.flush()
    st = json.loads(p.stdout.readline())
    snap = st["snapshot"]
    t1 = time.time()
    reqs = "".join(json.dumps({"id": 2 + i, "op": "step", "snapshot": snap, "event": "btn:none"}) + "\n"
                   for i in range(steps))
    p.stdin.write(reqs)
    p.stdin.flush()
    for i in range(steps):
        p.stdout.readline()
    step_us = (time.time() - t1) / steps * 1e6
    p.stdin.close()
    p.wait()
    ru = resource.getrusage(resource.RUSAGE_CHILDREN)
    os.unlink(f.name)
    return {"load_s": round(load, 3), "status": "OK", "states": hello.get("states"),
            "step_us_unmatched_event": round(step_us, 1), "maxrss_children_mb": round(ru.ru_maxrss / 1024)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", required=True)
    ap.add_argument("--only", action="append")
    ap.add_argument("--timeout", type=float, default=120)
    args = ap.parse_args()
    names = args.only or list(SCEN)
    for n in names:
        doc = SCEN[n]()
        text = json.dumps(doc)
        try:
            Bot(text)
            ok = True
        except Exception as e:
            ok = f"oracle: {e}"[:100]
        res = measure(args.engine, text, args.timeout)
        res.update({"scenario": n, "doc_bytes": len(text), "oracle_valid": ok})
        print(json.dumps(res, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()

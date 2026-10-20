#!/usr/bin/env python3
"""C6 (cross-verify, independent): document load time vs document size.

Generates flat documents of N messages (all in section `bot`):
  common : every message has the SAME two events btn:next / btn:prev (chain)
  unique : message i has its own event btn:b<i> (one unique button per message)
  nested : `common`, but messages split into sections of 10, each section has a
           section-level btn:cancel and a resume arrow (bubbling + resume rows)
Measures wall time of `figram check DOC` and of figram-engine load (process start
until the reply to {"op":"hello"}).  Every run is bounded by --cap seconds.

Usage: c6_load_perf.py FIGRAM ENGINE OUTDIR KIND N1 N2 ... [--cap 300] [--engine-only]
Prints one JSON line per (kind, N) and a log-log slope between consecutive sizes.
"""
import json
import math
import os
import subprocess
import sys
import time


def doc(kind, n):
    names = [f"m{i}" for i in range(n)]
    if kind in ("common", "unique"):
        ch = {}
        for i, m in enumerate(names):
            nxt, prv = names[(i + 1) % n], names[(i - 1) % n]
            if kind == "common":
                ch[m] = {"on": {"btn:next": [{"to": nxt}], "btn:prev": [{"to": prv}]}}
            else:
                ch[m] = {"on": {f"btn:b{i}": [{"to": nxt}]}}
        return {"figram": 1, "bot": {"initial": names[0], "children": ch}}
    if kind == "nested":
        secs = {}
        for s in range(0, n, 10):
            grp = names[s:s + 10]
            ch = {}
            for j, m in enumerate(grp):
                i = s + j
                ch[m] = {"on": {"btn:next": [{"to": names[(i + 1) % n]}],
                                "btn:prev": [{"to": names[(i - 1) % n]}]}}
            secs[f"s{s}"] = {"initial": grp[0], "children": ch,
                             "on": {"btn:cancel": [{"to": "home"}]}}
        secs["home"] = {"on": {"btn:back": [{"to": f"s{s}", "resume": True} for s in range(0, n, 10)][:1]}}
        return {"figram": 1, "bot": {"initial": "home", "children": secs}}
    raise SystemExit(f"unknown kind {kind}")


def timed(cmd, stdin_data, cap):
    t0 = time.monotonic()
    try:
        p = subprocess.run(cmd, input=stdin_data, capture_output=True, timeout=cap)
        return time.monotonic() - t0, p.returncode, p.stdout.decode(errors="replace").strip()[:120], \
            p.stderr.decode(errors="replace").strip()[:200]
    except subprocess.TimeoutExpired:
        return None, "timeout", "", ""


def main():
    args = sys.argv[1:]
    cap = 300.0
    engine_only = False
    if "--cap" in args:
        i = args.index("--cap"); cap = float(args[i + 1]); del args[i:i + 2]
    if "--engine-only" in args:
        args.remove("--engine-only"); engine_only = True
    figram, engine, out, kind = args[:4]
    ns = [int(x) for x in args[4:]]
    os.makedirs(out, exist_ok=True)
    prev = None
    for n in ns:
        path = os.path.join(out, f"{kind}-{n}.bot.json")
        with open(path, "w") as f:
            json.dump(doc(kind, n), f)
        size = os.path.getsize(path)
        tc = (None, "skipped", "", "")
        if not engine_only:
            tc = timed([figram, "check", path], None, cap)
        te = timed([engine, path], b'{"id":1,"op":"hello"}\n', cap)
        rec = {"kind": kind, "N": n, "bytes": size, "check_s": tc[0] and round(tc[0], 3), "check_rc": tc[1],
               "check_out": tc[2], "engine_load_s": te[0] and round(te[0], 3), "engine_rc": te[1],
               "engine_out": te[2], "engine_err": te[3], "loadavg": os.getloadavg()[0]}
        if prev and prev[1] and te[0]:
            rec["slope_engine_vs_prev"] = round(math.log(te[0] / prev[1]) / math.log(n / prev[0]), 2)
        if tc[0] and te[0]:
            rec["engine_over_check"] = round(te[0] / tc[0], 2)
        print(json.dumps(rec), flush=True)
        prev = (n, te[0])
        if te[0] is None:
            break


if __name__ == "__main__":
    main()

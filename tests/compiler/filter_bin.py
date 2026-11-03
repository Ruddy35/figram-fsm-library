#!/usr/bin/env python3
"""A deliberately broken `figram-engine` or `figram` for negative controls: runs the real
binary and damages its output in one named way.

  FILTER_REAL=BIN FILTER_DEFECT=D tests/compiler/filter_bin.py ARGS...

Engine defects (each reply line is changed; the protocol of docs/format.md, section 8):
  drop-detached      `"detached": true` removed from `call` actions
  drop-call-token    `token` removed from `call` actions
  reverse-pending    `pending` in the oldest-first order
  drop-pending       the snapshot's `pending` removed
  empty-pending      `"pending": []` written when there is no wait (bytes only)
  pending-after-seq  `pending` written after `seq` (bytes only)
  detached-false     `"detached": false` written on every sequential call
  keep-consumed      a timer removed by an arrow without a target is put back (needs the request)
figram defects (stderr of `figram check`):
  drop-outcome-warnings  the warnings "outcome ... has no arrow on ..." are dropped
The "bytes only" defects keep the parsed JSON equal: only a byte-for-byte comparison sees them."""

import json
import os
import subprocess
import sys

REAL = os.environ["FILTER_REAL"]
DEFECT = os.environ["FILTER_DEFECT"]


def damage(rep, req):
    snap = rep.get("snapshot")
    for a in rep.get("actions", []):
        if "call" in a:
            if DEFECT == "drop-detached":
                a.pop("detached", None)
            elif DEFECT == "drop-call-token":
                a.pop("token", None)
            elif DEFECT == "detached-false" and "detached" not in a:
                a["detached"] = False
    if isinstance(snap, dict):
        if DEFECT == "reverse-pending" and "pending" in snap:
            snap["pending"].reverse()
        elif DEFECT == "drop-pending":
            snap.pop("pending", None)
        elif DEFECT == "empty-pending" and "pending" not in snap:
            seq = snap.pop("seq", None)
            snap["pending"] = []
            if seq is not None:
                snap["seq"] = seq
        elif DEFECT == "pending-after-seq" and "pending" in snap:
            snap["pending"] = snap.pop("pending")
        elif DEFECT == "keep-consumed" and isinstance(req, dict) and req.get("op") == "step":
            ev, before = req.get("event", ""), req.get("snapshot", {}).get("timers", {})
            t = ev[len("timer:"):] if ev.startswith("timer:") else None
            if t in before and snap.get("at") == req["snapshot"].get("at") and t not in snap.get("timers", {}) \
                    and not any("cancel_timer" in a for a in rep.get("actions", [])):
                snap.setdefault("timers", {})[t] = before[t]
    return rep


def engine():
    p = subprocess.Popen([REAL] + sys.argv[1:], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
                         bufsize=1)
    for line in sys.stdin:
        p.stdin.write(line)
        p.stdin.flush()
        out = p.stdout.readline()
        if not out:
            break
        try:
            req = json.loads(line)
        except ValueError:
            req = None
        rep = json.loads(out)
        sys.stdout.write(json.dumps(damage(rep, req), separators=(",", ":"), ensure_ascii=False) + "\n")
        sys.stdout.flush()
    p.stdin.close()
    sys.exit(p.wait())


def figram():
    p = subprocess.run([REAL] + sys.argv[1:], capture_output=True, text=True)
    sys.stdout.write(p.stdout)
    for ln in p.stderr.splitlines(keepends=True):
        if DEFECT == "drop-outcome-warnings" and ln.startswith("warning: outcome ") and "has no arrow on" in ln:
            continue
        sys.stderr.write(ln)
    sys.exit(p.returncode)


if __name__ == "__main__":
    figram() if DEFECT == "drop-outcome-warnings" else engine()

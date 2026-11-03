#!/usr/bin/env python3
"""table.py: one row per defect: the suites run, their exit codes and failing counts."""
import glob, os, re
R = "/home/user/figram-audit/agents5/redteam"
def rc(path):
    m = re.search(r"^exit_code: (\d+)", open(path).read(), re.M)
    return int(m.group(1)) if m else None
def count(name, out):
    pats = {
        "conf": r"requests (\d+), mismatches (\d+)", "conf-golden": r"requests (\d+), mismatches (\d+)",
        "conf-traces": r"requests (\d+), mismatches (\d+)", "gate-conf": r"requests (\d+), mismatches (\d+)",
        "gate-traces": r"requests (\d+), mismatches (\d+)",
        "random": r"documents (\d+), requests \d+, documents with mismatches (\d+)",
        "gate-random": r"documents (\d+), requests \d+, documents with mismatches (\d+)",
        "edges": r"protocol edge cases (\d+), replies \d+, mismatches (\d+)",
        "snap": r"snapshots (\d+), replies \d+, schema/engine mismatches (\d+)",
        "directed": r"cases run: (\d+) .*failed: (\d+)", "directed-findings": r"cases run: (\d+) .*failed: (\d+)",
        "d-engine": r"requests=(\d+) replies=\d+ mismatches=(\d+)",
    }
    if name in pats:
        m = re.search(pats[name], out)
        return f"{m.group(2)}/{m.group(1)}" if m else "?"
    if name.startswith("diff"):
        n = len(re.findall(r"^DIVERGENCE", out, re.M))
        m = re.search(r'"divergences": (\d+)', out)
        return f"div={m.group(1) if m else n}"
    if name == "host":
        e = open(os.path.join(os.path.dirname(out_path_holder[0]), "host.expect")).read().strip() if os.path.exists(os.path.join(os.path.dirname(out_path_holder[0]), "host.expect")) else ""
        return e.split("\n")[-1]
    if name == "formats":
        m = re.search(r"unexpected exit codes: (\d+)", out); return f"unexpected={m.group(1) if m else '?'}"
    n = len(re.findall(r"^\s*FAIL", out, re.M))
    return f"FAIL={n}"
out_path_holder = [""]
for d in sorted(glob.glob(f"{R}/LOGS/d*/")):
    name = os.path.basename(d.rstrip("/"))
    cells = []
    for log in sorted(glob.glob(d + "*.log")):
        s = os.path.basename(log)[:-4]
        if s in ("host",) or True:
            pass
        out_path_holder[0] = log
        out = open(log[:-4] + ".stdout", errors="replace").read() if os.path.exists(log[:-4] + ".stdout") else ""
        c = rc(log)
        cells.append(f"{s}:{'RED' if c else 'green'}({count(s, out)})")
    print(name, " ".join(cells))

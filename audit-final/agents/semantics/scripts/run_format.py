#!/usr/bin/env python3
"""Run `figram check` on every generated case; compare with the prose verdict; also report the schema verdict."""
import json, subprocess, sys
import jsonschema
OUT = "/home/user/figram-audit/agents5/semantics/REPRODUCTIONS/format"
FIG = "/home/user/figram-audit-worktrees/a5-semantics/.lake/build/bin/figram"
schema = json.load(open("/home/user/figram-audit-worktrees/a5-semantics/schemas/figram.schema.json"))
V = jsonschema.Draft202012Validator(schema)
bad = 0
for c in json.load(open(f"{OUT}/cases.json")):
    p = f"{OUT}/{c['name']}.bot.json"
    r = subprocess.run(["timeout", "20", FIG, "check", p], capture_output=True, text=True)
    got = "OK" if r.returncode == 0 else "E"
    try:
        sv = "OK" if V.is_valid(json.load(open(p))) else "E"
    except Exception as e:
        sv = "?"
    mark = "ok " if got == c["expect"] else "MISMATCH"
    if got != c["expect"]: bad += 1
    msg = (r.stdout + r.stderr).strip().replace("\n", " | ")
    print(f"{mark} {c['name']}: expect={c['expect']} check={got}(rc={r.returncode}) schema={sv}{'  SCHEMA-DIFFERS' if sv != got else ''} :: {msg[:300]}")
print("mismatches", bad)
sys.exit(1 if bad else 0)

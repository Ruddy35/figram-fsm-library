#!/usr/bin/env python3
"""R08: does the shipped VERIFY.log correspond to the shipped verify.sh?
Checks (1) every `step "..."` header and (2) every fixed success `echo "..."` line of verify.sh
(no $-expansion) against VERIFY.log; (3) archive mtimes (zip) of VERIFY.log vs files changed after it.
Usage: python3 -I r08_verifylog_provenance.py <repo-root> [archive.zip]"""
import re, sys, zipfile, datetime
R = sys.argv[1]
sh = open(f"{R}/verify.sh", encoding="utf-8").read()
log = open(f"{R}/VERIFY.log", encoding="utf-8").read()
missing = []
steps = re.findall(r'^\s*step "([^"]+)"', sh, re.M)
for s in steps:
    if f"== {s}" not in log: missing.append(("step", s))
for line in re.findall(r'^\s*(?:else )?echo "([^"$]+)"', sh, re.M):
    if line.startswith(("НЕТ", "ожидалась", "нужен", "--- хвост")):   # failure-path messages
        continue
    if line not in log: missing.append(("echo", line))
print(f"verify.sh steps: {len(steps)}; VERIFY.log step headers: {log.count(chr(10)+'== ')+log.startswith('== ')}")
for kind, s in missing: print(f"MISSING in VERIFY.log ({kind}): {s}")
if len(sys.argv) > 2:
    z = zipfile.ZipFile(sys.argv[2]); t = {i.filename.split('/',1)[1]: datetime.datetime(*i.date_time) for i in z.infolist() if not i.is_dir()}
    vt = t["VERIFY.log"]; print(f"VERIFY.log mtime in archive: {vt}")
    for f, m in sorted(t.items(), key=lambda kv: kv[1]):
        if m > vt and not f.startswith(".git/"): print(f"  changed AFTER VERIFY.log: {m}  {f}")
sys.exit(1 if missing else 0)

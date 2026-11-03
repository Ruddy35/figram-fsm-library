#!/usr/bin/env python3
"""auto_sorry.py NAME TARGET...: build TARGETs; while a proof fails, stub the enclosing theorem with
`sorry` (experiment only) and rebuild. Prints the list of stubbed theorems (= the proofs the defect
breaks). Each build is logged with run.sh into LOGS/build-NAME-<i>."""
import os, re, subprocess, sys
R = "/home/user/figram-audit/agents5/redteam"
W = "/home/user/figram-audit-worktrees/a5-redteam"
name, targets = sys.argv[1], sys.argv[2:]
env = dict(os.environ)
env["PATH"] = "/home/user/leanprover/lean4/build/release/stage1/bin:" + env["PATH"]
env["LEAN_CC"] = "/home/user/figram-audit/fixes/memory/cc-quiet.sh"
stubbed = []
listfile = f"{R}/LOGS/{name}.stubbed"
if os.path.exists(listfile):
    stubbed = [l.rstrip("\n") for l in open(listfile) if l.strip()]
for i in range(40):
    log = f"{R}/LOGS/build-{name}-{'-'.join(targets) or 'all'}-{i}"
    rc = subprocess.run(["/home/user/figram-audit/run.sh", log, "nice", "-n", "10", "timeout", "1500",
                         "lake", "build", *targets], cwd=W, env=env, stdout=subprocess.DEVNULL).returncode
    out = open(log + ".stdout").read()
    if rc == 0:
        print(f"build ok after {i} rounds; stubbed: {stubbed}")
        break
    errs = re.findall(r"error: (Figram/\w+\.lean|Main\.lean|EngineMain\.lean):(\d+):\d+", out)
    if not errs:
        print("build failed without a located error:", out[-2000:]); sys.exit(2)
    for f, ln in sorted({(f, int(ln)) for f, ln in errs}, key=lambda x: (x[0], -x[1])):
        lines = open(os.path.join(W, f)).read().split("\n")
        j = ln - 1
        if lines[j].startswith("#guard"):
            key = f"{f}:#guard@{ln}:{lines[j][7:70]}"
            k = j + 1
            while k < len(lines) and lines[k].startswith((" ", "\t")) and lines[k].strip():
                k += 1
            for q in range(j, k):
                lines[q] = "-- GUARD-OFF " + lines[q]
            open(os.path.join(W, f), "w").write("\n".join(lines))
            stubbed.append(key)
            print("guard off", key)
            continue
        while j >= 0 and not re.match(r"^(@\[[^\]]*\]\s*)?((private|protected)\s+)?(theorem|def|abbrev|instance|structure|inductive)\s", lines[j]):
            j -= 1
        m = re.match(r"^(@\[[^\]]*\]\s*)?((private|protected)\s+)?(theorem|def|abbrev|instance|structure|inductive)\s+(\S+)", lines[j])
        if not m or m.group(4) != "theorem":
            print(f"error outside a theorem at {f}:{ln}: {lines[j] if j>=0 else ''}"); sys.exit(3)
        thm = m.group(5)
        key = f"{f}:{thm}"
        if key in stubbed:
            continue
        subprocess.run(["python3", f"{R}/scripts/sorry_thm.py", os.path.join(W, f), thm], check=True, stdout=subprocess.DEVNULL)
        stubbed.append(key)
        print("stubbed", key, "(error at line", ln, ")")
    open(listfile, "w").write("\n".join(stubbed) + "\n")
else:
    print("gave up"); sys.exit(4)

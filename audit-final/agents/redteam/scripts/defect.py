#!/usr/bin/env python3
"""defect.py NAME [--format]: inject the defect DEFECTS/NAME.edit.py (EDITS = [(file, old, new), ...]),
build figram-engine and then everything with failing proofs stubbed (auto_sorry.py), save the binaries
in bin/mut/NAME (figram-engine; figram as `figram` with --format, else as `figram-gate`), the patches
DEFECTS/NAME.patch (defect only) and NAME.full.patch (with the stubs), then restore the sources."""
import os, shutil, subprocess, sys
R = "/home/user/figram-audit/agents5/redteam"
W = "/home/user/figram-audit-worktrees/a5-redteam"
name = sys.argv[1]; fmt = "--format" in sys.argv
def sh(*a, **k): return subprocess.run(list(a), cwd=W, **k)
assert sh("git", "status", "--porcelain", "--untracked-files=no", capture_output=True, text=True).stdout == "", "tree not clean"
ns = {}; exec(open(f"{R}/DEFECTS/{name}.edit.py").read(), ns)
for f, old, new in ns["EDITS"]:
    p = os.path.join(W, f); s = open(p).read()
    assert s.count(old) == 1, f"{f}: pattern occurs {s.count(old)} times: {old[:80]!r}"
    open(p, "w").write(s.replace(old, new))
open(f"{R}/DEFECTS/{name}.patch", "w").write(sh("git", "diff", capture_output=True, text=True).stdout)
try:
    if os.path.exists(f"{R}/LOGS/{name}.stubbed"): os.remove(f"{R}/LOGS/{name}.stubbed")
    out = os.path.join(R, "bin", "mut", name); os.makedirs(out, exist_ok=True)
    r1 = sh("python3", f"{R}/scripts/auto_sorry.py", name, "figram-engine")
    if r1.returncode != 0: sys.exit("engine build failed")
    shutil.copy(os.path.join(W, ".lake/build/bin/figram-engine"), out)
    eng = open(f"{R}/LOGS/{name}.stubbed").read().splitlines() if os.path.exists(f"{R}/LOGS/{name}.stubbed") else []
    open(f"{R}/LOGS/{name}.stubbed-engine", "w").write("\n".join(eng) + "\n")
    r2 = sh("python3", f"{R}/scripts/auto_sorry.py", name)
    if r2.returncode == 0:
        shutil.copy(os.path.join(W, ".lake/build/bin/figram"), os.path.join(out, "figram" if fmt else "figram-gate"))
    else:
        print("full build failed")
    open(f"{R}/DEFECTS/{name}.full.patch", "w").write(sh("git", "diff", capture_output=True, text=True).stdout)
finally:
    sh("git", "checkout", "--", "Figram", "Main.lean", "EngineMain.lean")
    st = sh("git", "status", "--porcelain", "--untracked-files=no", capture_output=True, text=True).stdout
    print("restored; git status:", st or "clean")

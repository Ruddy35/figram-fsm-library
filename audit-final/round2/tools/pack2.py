#!/usr/bin/env python3
"""Pack the second round of the 0.3.0 audit into <dest>/audit-final/round2.

Usage: pack2.py DEST_WORKTREE
Copies the agents' outputs (agents6), the orchestrator's logs and tools; excludes ELF binaries,
SQLite databases of the runs and __pycache__ (their sha256 go to MANIFEST.json); gzips files over
3 MB; writes MANIFEST.json.
"""
import datetime
import gzip
import hashlib
import json
import os
import shutil
import subprocess
import sys

DEST = os.path.join(sys.argv[1], "audit-final", "round2")
A = "/home/user/figram-audit"
STAGE = os.path.join(A, "archive030", "round2")
SP = "/tmp/claude-0/-home-user/57129652-0dc1-5f90-93b6-ee8cd24dbb93/scratchpad"
REPO = "/home/user/figram-audit-worktrees/cleanup-int"
LIMIT = 3 * 1024 * 1024

AGENTS = {name: os.path.join(A, "agents6", name) for name in ("recheck", "hostpy", "hostgo")}
LOGS = {
    "v030-r2-gate": os.path.join(A, "cleanup", "LOGS", "v030-r2-gate"),
    "r2-fixes": os.path.join(A, "cleanup", "LOGS", "r2-fixes"),
}
TOOLS = [os.path.join(A, "BRIEF6.md"), os.path.join(A, "run.sh"), os.path.join(SP, "gate_v030r2.sh"),
         os.path.join(STAGE, "pack2.py"), os.path.join(SP, "r2", "limit_guard.py"),
         os.path.join(SP, "r2", "maps_probe.py"), os.path.join(SP, "r2", "maps_detail.py"),
         os.path.join(SP, "neg", "drop_new_wait.py")]

excluded = []


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def is_elf(path):
    with open(path, "rb") as f:
        return f.read(4) == b"\x7fELF"


def is_db(path):
    n = os.path.basename(path)
    return n.endswith((".db", ".db-wal", ".db-shm", ".sqlite"))


def copy_file(src, dst, rel):
    if is_elf(src):
        excluded.append({"path": rel, "sha256": sha(src), "size": os.path.getsize(src),
                         "reason": "compiled binary (rebuilt from the sources)"})
        return
    if is_db(src):
        excluded.append({"path": rel, "sha256": sha(src), "size": os.path.getsize(src),
                         "reason": "SQLite database of a run (the run's summary is kept)"})
        return
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if os.path.getsize(src) > LIMIT and not src.endswith(".gz"):
        with open(src, "rb") as f, gzip.open(dst + ".gz", "wb", compresslevel=9) as g:
            shutil.copyfileobj(f, g)
    else:
        shutil.copy2(src, dst)


def copy_tree(src, dst, relbase):
    for root, dirs, files in os.walk(src):
        dirs[:] = sorted(d for d in dirs if d not in ("__pycache__", ".git", ".lake"))
        for name in sorted(files):
            s = os.path.join(root, name)
            if os.path.islink(s):
                excluded.append({"path": os.path.join(relbase, os.path.relpath(s, src)),
                                 "reason": "symbolic link", "target": os.readlink(s)})
                continue
            rel = os.path.relpath(s, src)
            copy_file(s, os.path.join(dst, rel), os.path.join(relbase, rel))


if os.path.exists(DEST):
    sys.exit(f"{DEST} exists")
os.makedirs(DEST)
for f in ("README.md", "EXECUTIVE.md", "FIXES.md", "TEST_MATRIX.md"):
    shutil.copy2(os.path.join(STAGE, f), os.path.join(DEST, f))
for name, src in AGENTS.items():
    d = os.path.join(DEST, "agents", name)
    copy_tree(src, d, f"agents/{name}")
    shutil.copy2(os.path.join(STAGE, "reports", f"{name}.md"), os.path.join(d, "REPORT.md"))
for name, src in LOGS.items():
    copy_tree(src, os.path.join(DEST, "LOGS", "orchestrator", name), f"LOGS/orchestrator/{name}")
os.makedirs(os.path.join(DEST, "tools"))
for t in TOOLS:
    shutil.copy2(t, os.path.join(DEST, "tools", os.path.basename(t)))


def git(*a):
    return subprocess.run(["git", "-C", REPO] + list(a), capture_output=True, text=True, check=True).stdout.strip()


files = {}
for root, dirs, names in os.walk(DEST):
    dirs.sort()
    for n in sorted(names):
        p = os.path.join(root, n)
        files[os.path.relpath(p, DEST)] = sha(p)
manifest = {
    "generated_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
    "repository": "Ruddy35/figram-fsm-library",
    "audited": "Figram 0.3.0 (unreleased), second round: recheck of the first round's fixes, "
               "a production-like Python host, an embedded Go host",
    "audited_commit": "598fec6",
    "published_branch": "claude/kind-ramanujan-n4q8mg",
    "fix_commits": git("log", "--format=%h %s", "598fec6..HEAD").splitlines(),
    "final_commit": git("rev-parse", "HEAD"),
    "agent_branches": {b: git("rev-parse", b) for b in ("audit6/recheck", "audit6/hostpy", "audit6/hostgo")},
    "agent_branches_note": "the agents changed no product code: their branches stay at 598fec6, no bundle",
    "ci": {"run": 41770701425, "run_number": 47, "commit": "ebdfe90", "conclusion": "success"},
    "local_gate": {"commit": "ebdfe90", "log": "LOGS/orchestrator/v030-r2-gate", "steps": 13, "all_exit_codes": 0},
    "toolchains": {"lean": "Lean 4.35.0, commit eb6cafb7290e8bd5f2937522d8d804f58f02bafc, built from source",
                   "gcc": "13.3.0", "go": "go1.24.7", "python": "3.13.16"},
    "machine": "KVM 4 vCPU, Linux 6.18.44 (shared by the agents)",
    "redactions": [],
    "excluded": excluded,
    "files_sha256": files,
}
with open(os.path.join(DEST, "MANIFEST.json"), "w", encoding="utf-8") as f:
    json.dump(manifest, f, indent=1, ensure_ascii=False)
    f.write("\n")
print(f"files {len(files)}, excluded {len(excluded)}")

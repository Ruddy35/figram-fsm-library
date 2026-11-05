#!/usr/bin/env python3
"""Pack the audit of ffl-go 0.3.0 into <dest>/audit-final/ffl-go-0.3.0.

Usage: pack_fflgo.py DEST_WORKTREE
Copies the agent's outputs (agents7/fflgo), the orchestrator's logs (ffl-go-030) and the tools;
excludes ELF binaries, the release's static libraries (ar archives) in the agent's module copies,
SQLite databases and __pycache__ (their sha256 go to MANIFEST.json); gzips files over 3 MB;
writes MANIFEST.json.
"""
import datetime
import gzip
import hashlib
import json
import os
import shutil
import subprocess
import sys

DEST = os.path.join(sys.argv[1], "audit-final", "ffl-go-0.3.0")
A = "/home/user/figram-audit"
STAGE = os.path.join(A, "archive030", "fflgo")
SP = "/tmp/claude-0/-home-user/57129652-0dc1-5f90-93b6-ee8cd24dbb93/scratchpad"
FFLGO = "/home/user/ffl-go"
FIGRAM = "/home/user/figram-audit-worktrees/cleanup-int"
LIMIT = 3 * 1024 * 1024

AGENT = os.path.join(A, "agents7", "fflgo")
LOGS = os.path.join(A, "cleanup", "LOGS", "ffl-go-030")
TOOLS = {
    "BRIEF7.md": os.path.join(A, "BRIEF7.md"),
    "run.sh": os.path.join(A, "run.sh"),
    "pack_fflgo.py": os.path.join(STAGE, "pack_fflgo.py"),
    "proxy-consumer/check.sh": os.path.join(SP, "fflgo-v030-consumer", "check.sh"),
    "proxy-consumer/main.go": os.path.join(SP, "fflgo-v030-consumer", "main.go"),
    "agent_mutants_fixed.py": os.path.join(SP, "agentmut", "agent_mutants_fixed.py"),
    "ws-probe/zz_wsprobe_test.go": os.path.join(SP, "wsprobe", "mod", "zz_wsprobe_test.go"),
    "ws-probe/pyprobe.py": os.path.join(SP, "wsprobe", "pyprobe.py"),
}

excluded = []


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def magic(path, n):
    with open(path, "rb") as f:
        return f.read(n)


def copy_file(src, dst, rel):
    if magic(src, 4) == b"\x7fELF":
        excluded.append({"path": rel, "sha256": sha(src), "size": os.path.getsize(src),
                         "reason": "compiled binary (go build from the sources next to it)"})
        return
    if magic(src, 8) == b"!<arch>\n":
        excluded.append({"path": rel, "sha256": sha(src), "size": os.path.getsize(src),
                         "reason": "static library of the Figram v0.3.0 release, unchanged "
                                   "(ffl-go lib/linux_amd64; scripts/update-lib.sh --check)"})
        return
    if os.path.basename(src).endswith((".db", ".db-wal", ".db-shm", ".sqlite")):
        excluded.append({"path": rel, "sha256": sha(src), "size": os.path.getsize(src),
                         "reason": "SQLite database of a run"})
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
copy_tree(AGENT, os.path.join(DEST, "agents", "fflgo"), "agents/fflgo")
shutil.copy2(os.path.join(STAGE, "REPORT.md"), os.path.join(DEST, "agents", "fflgo", "REPORT.md"))
copy_tree(LOGS, os.path.join(DEST, "LOGS", "orchestrator", "ffl-go-030"), "LOGS/orchestrator/ffl-go-030")
for name, src in TOOLS.items():
    dst = os.path.join(DEST, "tools", name)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copy2(src, dst)


def git(repo, *a):
    return subprocess.run(["git", "-C", repo] + list(a), capture_output=True, text=True, check=True).stdout.strip()


files = {}
for root, dirs, names in os.walk(DEST):
    dirs.sort()
    for n in sorted(names):
        p = os.path.join(root, n)
        files[os.path.relpath(p, DEST)] = sha(p)
manifest = {
    "generated_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
    "repository": "Ruddy35/ffl-go",
    "audited": "ffl-go 0.3.0 (unreleased): the update to Figram v0.3.0 and engine protocol 3",
    "audited_commit": git(FFLGO, "rev-parse", "51c2312"),
    "fix_commit": git(FFLGO, "rev-parse", "361815c"),
    "merge_commit": "777d04c91e66631a86cb13a68bae16788726adea",
    "merge_tree_equals_fix_tree": git(FFLGO, "rev-parse", "361815c^{tree}"),
    "pull_request": "https://github.com/Ruddy35/ffl-go/pull/3",
    "release": {"tag": "v0.3.0", "commit": "777d04c91e66631a86cb13a68bae16788726adea",
                "published_utc": "2026-11-05T01:24:49Z",
                "url": "https://github.com/Ruddy35/ffl-go/releases/tag/v0.3.0",
                "workflow_run": 41947432671},
    "ci": [{"run": 41941023187, "run_number": 13, "event": "push", "commit": "361815c", "conclusion": "success"},
           {"run": 41941027667, "run_number": 14, "event": "pull_request", "commit": "361815c", "conclusion": "success"},
           {"run": 41941458376, "run_number": 15, "event": "push master", "commit": "777d04c", "conclusion": "success"},
           {"run": 41948350922, "run_number": 16, "event": "push tag v0.3.0", "commit": "777d04c", "conclusion": "success"}],
    "figram_docs": {"repository": "Ruddy35/figram-fsm-library", "commit": git(FIGRAM, "rev-parse", "5bf932c"),
                    "pull_request": "https://github.com/Ruddy35/figram-fsm-library/pull/12",
                    "merge_commit": "af0db04"},
    "agent_branch": {"name": "audit7/fflgo", "repository": "ffl-go",
                     "commit": git("/home/user/figram-audit-worktrees/a7-fflgo", "rev-parse", "HEAD"),
                     "note": "the agent changed no code of the module: the branch stays at 51c2312, no bundle"},
    "toolchains": {"go": "go1.24.7", "python": "3.13.16", "gcc": "13.3.0"},
    "machine": "KVM 4 vCPU, Linux 6.18.44 (shared)",
    "redactions": [],
    "excluded": excluded,
    "files_sha256": files,
}
with open(os.path.join(DEST, "MANIFEST.json"), "w", encoding="utf-8") as f:
    json.dump(manifest, f, indent=1, ensure_ascii=False)
    f.write("\n")
print(f"files {len(files)}, excluded {len(excluded)}")

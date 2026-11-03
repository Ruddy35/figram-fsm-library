#!/usr/bin/env python3
"""Pack the 0.3.0 audit materials into <dest>/audit-final (layout of the audit-2026-10-20 archive).

Usage: pack.py DEST_WORKTREE
Copies the agents' outputs, the orchestrator's logs and tools; excludes ELF binaries and
__pycache__ (their sha256 go to MANIFEST.json); gzips files over 3 MB; writes MANIFEST.json.
"""
import datetime
import gzip
import hashlib
import json
import os
import shutil
import subprocess
import sys

DEST = os.path.join(sys.argv[1], "audit-final")
A = "/home/user/figram-audit"
STAGE = os.path.join(A, "archive030")
SP = "/tmp/claude-0/-home-user/57129652-0dc1-5f90-93b6-ee8cd24dbb93/scratchpad"
LIMIT = 3 * 1024 * 1024

AGENTS = {
    "impl-oracle": os.path.join(A, "agents4", "oracle"),
    "impl-protocol": os.path.join(A, "agents4", "protocol"),
    "formal": os.path.join(A, "agents5", "formal"),
    "semantics": os.path.join(A, "agents5", "semantics"),
    "protocol": os.path.join(A, "agents5", "protocol"),
    "redteam": os.path.join(A, "agents5", "redteam"),
}
LOGS = {
    "v030-final-gate2": os.path.join(A, "cleanup", "LOGS", "v030-final-gate2"),
    "v030-final-gate": os.path.join(A, "cleanup", "LOGS", "v030-final-gate"),
    "v030-pre": os.path.join(A, "cleanup", "LOGS", "v030-pre"),
    "v030-pre2": os.path.join(A, "cleanup", "LOGS", "v030-pre2"),
    "scratch": os.path.join(SP, "logs4"),
}
TOOLS = [os.path.join(A, "BRIEF4.md"), os.path.join(A, "BRIEF5.md"), os.path.join(A, "run.sh"),
         os.path.join(SP, "gate_v030b.sh"), os.path.join(STAGE, "pack.py")]

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


def copy_file(src, dst, rel):
    if is_elf(src):
        excluded.append({"path": rel, "sha256": sha(src), "size": os.path.getsize(src),
                         "reason": "compiled binary (rebuilt from the sources and patches)"})
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

repo = sys.argv[1]
os.makedirs(os.path.join(DEST, "PATCHES"))
branches = ["audit4/oracle", "audit4/protocol", "audit5/formal", "audit5/semantics",
            "audit5/protocol", "audit5/redteam"]
subprocess.run(["git", "-C", repo, "bundle", "create", os.path.join(DEST, "PATCHES", "audit-branches.bundle"),
                "^85bae04"] + branches, check=True, capture_output=True)
heads = {b: subprocess.run(["git", "-C", repo, "rev-parse", b], capture_output=True, text=True,
                           check=True).stdout.strip() for b in branches}

files = {}
for root, dirs, names in os.walk(DEST):
    dirs.sort()
    for n in sorted(names):
        p = os.path.join(root, n)
        files[os.path.relpath(p, DEST)] = sha(p)
manifest = {
    "generated_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
    "repository": "Ruddy35/figram-fsm-library",
    "audited": "Figram 0.3.0 (unreleased): background actions, actions with outcomes, arrows without a target, protocol 3",
    "published_branch": "claude/kind-ramanujan-n4q8mg",
    "base_master": "85bae04 (merge of PR #7, release 0.2.0 line)",
    "final_commit": subprocess.run(["git", "-C", repo, "rev-parse", "HEAD"], capture_output=True,
                                   text=True, check=True).stdout.strip(),
    "audit_bases": {"implementation_wave": "85371f5", "final_audit": "7239ab7 (formal, semantics, protocol), 6d8d4c1 (redteam)"},
    "agent_branch_heads": heads,
    "ci": {"run": 41707449295, "run_number": 41, "commit": "598fec6", "conclusion": "success"},
    "local_gate": {"commit": "598fec6", "log": "LOGS/orchestrator/v030-final-gate2", "steps": 13, "all_exit_codes": 0},
    "toolchains": {"lean": "Lean 4.35.0, commit eb6cafb7290e8bd5f2937522d8d804f58f02bafc, built from source",
                   "gcc": "13.3.0", "go": "go1.24.7", "python": "3.13.16", "jsonschema": "4.26.0"},
    "machine": "KVM 4 vCPU, Linux 6.18.44 (shared by the agents)",
    "redactions": ["agents/protocol/REPORT.md item P3 (11): a performance class tracked in the private security advisory"],
    "excluded": excluded,
    "files_sha256": files,
}
with open(os.path.join(DEST, "MANIFEST.json"), "w", encoding="utf-8") as f:
    json.dump(manifest, f, indent=1, ensure_ascii=False)
    f.write("\n")
print(f"files {len(files)}, excluded {len(excluded)}")

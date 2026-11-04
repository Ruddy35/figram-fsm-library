#!/usr/bin/env python3
"""A broken engine for the negative control of I6: runs the real figram-engine and corrupts every
Nth reply that carries a `call` action with a token (the token becomes -1)."""
import os, re, subprocess, sys
REPO = os.environ.get("FIGRAM_REPO", "/home/user/figram-audit-worktrees/a6-hostpy")
N = int(os.environ.get("BADENGINE_EVERY", "40"))
p = subprocess.Popen([os.path.join(REPO, ".lake/build/bin/figram-engine")] + sys.argv[1:],
                     stdin=sys.stdin, stdout=subprocess.PIPE)
k = 0
for line in p.stdout:
    if b'"token":' in line and b'"call":' in line:
        k += 1
        if k % N == 0:
            line = re.sub(rb'("call":"[^"]+","token":)\d+', rb'\1-1', line, count=1)
    sys.stdout.buffer.write(line)
    sys.stdout.buffer.flush()
sys.exit(p.wait())

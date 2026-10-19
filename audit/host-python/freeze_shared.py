#!/usr/bin/env python3
"""Write (or verify with --check) /home/user/figram-audit/hosts/shared/VERSION: freeze marker with the
SHA-256 of every shared artefact. The corpus is regenerated in a temp dir and must be byte-identical."""
import hashlib
import os
import subprocess
import sys
import tempfile

SHARED = "/home/user/figram-audit/hosts/shared"
HERE = os.path.dirname(os.path.abspath(__file__))


def sha(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest()


def files():
    out = []
    for sub in ("bots", "corpus"):
        for f in sorted(os.listdir(os.path.join(SHARED, sub))):
            out.append(f"{sub}/{f}")
    return out + ["domain.md", "README.md"]


def main():
    with tempfile.TemporaryDirectory() as td:
        subprocess.check_call([sys.executable, os.path.join(HERE, "gen_corpus.py"), td], stdout=subprocess.DEVNULL)
        for f in sorted(os.listdir(td)):
            if sha(os.path.join(td, f)) != sha(os.path.join(SHARED, "corpus", f)):
                sys.exit(f"corpus/{f} differs from a fresh regeneration")
    lines = [f"{sha(os.path.join(SHARED, f))}  {f}" for f in files()]
    if "--check" in sys.argv:
        want = [x for x in open(os.path.join(SHARED, "VERSION")).read().splitlines() if x and x[0] in "0123456789abcdef"]
        if want != lines:
            sys.exit("VERSION mismatch: " + str(set(want) ^ set(lines)))
        print(f"ok: {len(lines)} shared files match VERSION")
        return
    commit = subprocess.check_output(["git", "-C", HERE, "rev-parse", "HEAD"], text=True).strip()
    head = [f"FROZEN v1 — shared artefacts for the Go/Python production-like hosts",
            f"generator: audit/host-python/gen_corpus.py @ branch audit/host-python commit {commit}",
            "verify: python3 audit/host-python/freeze_shared.py --check", ""]
    open(os.path.join(SHARED, "VERSION"), "w").write("\n".join(head + lines) + "\n")
    print(f"wrote VERSION with {len(lines)} files")


if __name__ == "__main__":
    main()

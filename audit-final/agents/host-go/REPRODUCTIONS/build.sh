#!/usr/bin/env bash
# Builds the Go host and probes of agent host-go against the REAL libfigram.a
# (worktree ffi/out = copy of /home/user/figram-audit/fresh/ffi-out).
set -euo pipefail
W=/home/user/figram-audit-worktrees/host-go
source /home/user/figram-audit/env.sh
export CGO_LDFLAGS=-lstdc++
cd "$W/audit/host-go"
for c in fghost fgrun fgprobe fgmem fgsigprobe; do nice -n 10 go build -o "bin/$c" "./cmd/$c"; done
sha256sum "$W/ffi/out/libfigram.a" /home/user/figram-audit/fresh/ffi-out/libfigram.a bin/fghost

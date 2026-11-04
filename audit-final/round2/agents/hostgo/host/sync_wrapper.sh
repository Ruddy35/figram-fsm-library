#!/usr/bin/env bash
# Copies the reference Go wrapper (clients/go/main.go of the audited worktree) into package
# refwrap, unchanged except for the package clause, so the host uses exactly the reference code.
set -euo pipefail
W=${FIGRAM_WT:-/home/user/figram-audit-worktrees/a6-hostgo}
D=$(cd "$(dirname "$0")" && pwd)
sed 's/^package main$/package refwrap/' "$W/clients/go/main.go" > "$D/refwrap/wrapper.go"
diff <(sed 's/^package main$/package refwrap/' "$W/clients/go/main.go") "$D/refwrap/wrapper.go"
echo "refwrap/wrapper.go = clients/go/main.go @ $(git -C "$W" rev-parse --short HEAD) (package clause renamed)"

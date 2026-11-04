#!/usr/bin/env bash
cd "$(dirname "$0")"
W=/home/user/figram-audit-worktrees/a6-hostgo
CGO_CFLAGS="-I$W/ffi" CGO_LDFLAGS="$(cat $W/ffi/out/LDFLAGS)" GOPROXY=off GOFLAGS=-mod=mod go build -o /dev/null . 

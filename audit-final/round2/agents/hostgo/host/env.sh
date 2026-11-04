# Build environment as docs/embedding.md prescribes for "your own module":
# CGO_CFLAGS="-I/path/to/figram/ffi" CGO_LDFLAGS="$(cat /path/to/figram/ffi/out/LDFLAGS)"
W=${FIGRAM_WT:-/home/user/figram-audit-worktrees/a6-hostgo}
export CGO_CFLAGS="-I$W/ffi -O2"
export CGO_LDFLAGS="$(cat $W/ffi/out/LDFLAGS)"
export GOPROXY=off GOFLAGS=-mod=mod

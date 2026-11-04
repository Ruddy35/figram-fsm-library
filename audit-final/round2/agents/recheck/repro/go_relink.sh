#!/usr/bin/env bash
# RT-P1-2 recheck: does verify.sh relink clients/go/gofig when only ffi/out/libfigram.a changes?
#   go_relink.sh GOTREE LIB_A LIB_B
# GOTREE has clients/go and ffi/{out,figram.h} (a scratch copy). LIB_B differs from LIB_A only in
# figram_ffi.o, whose "empty request" text is "EMPTY REQUEST LIB-B".
# 1. pre-fix command (7239ab7 verify.sh): build with A, swap in B, build again → is B linked in?
# 2. post-fix command (598fec6 verify.sh): the same with rm -f and -X main.figramLib=<hash>.
set -u
G=$1; A=$2; B=$3
cd "$G/clients/go" || exit 2
has_b() { strings gofig | grep -q "EMPTY REQUEST LIB-B" && echo yes || echo no; }
old_build() { CC=${CC:-gcc} go build -ldflags=-extldflags=-Wl,--gc-sections -o gofig . ; }
new_build() {
  LIBHASH=$(sha256sum ../../ffi/out/libfigram.a | cut -c1-16)
  rm -f gofig gofig-race
  CC=${CC:-gcc} go build -ldflags="-extldflags=-Wl,--gc-sections -X main.figramLib=$LIBHASH" -o gofig .
}
rc=0
cp "$A" ../../ffi/out/libfigram.a; rm -f gofig; old_build; echo "pre-fix:  built with A, contains LIB-B: $(has_b)"
cp "$B" ../../ffi/out/libfigram.a; old_build; r1=$(has_b); echo "pre-fix:  library replaced by B, rebuilt, contains LIB-B: $r1"
cp "$A" ../../ffi/out/libfigram.a; new_build; echo "post-fix: built with A, contains LIB-B: $(has_b)"
cp "$B" ../../ffi/out/libfigram.a; new_build; r2=$(has_b); echo "post-fix: library replaced by B, rebuilt, contains LIB-B: $r2"
cp "$A" ../../ffi/out/libfigram.a
[ "$r1" = no ] && echo "the pre-fix command keeps the stale binary (the defect RT-P1-2 reproduces)"
[ "$r2" = yes ] && echo "the post-fix command relinks with the new library" || rc=1
exit $rc

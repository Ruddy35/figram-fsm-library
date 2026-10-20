#!/usr/bin/env bash
# FFI-12: кэш сборки Go не замечает изменения libfigram.a (cgo -l): после пересборки
# библиотеки go build / go test -c могут слинковать старую. Нужны: ffi/audit/out (build_audit.sh)
# с ИСПРАВЛЕННОЙ прослойкой (символ init_state) и baseline-прослойка (символ ready).
#   REPRODUCTIONS/go_stale_cgo_lib.sh /home/user/figram-audit-worktrees/ffi-go
set -eu
W=${1:-/home/user/figram-audit-worktrees/ffi-go}
S=$(mktemp -d)
export GOCACHE=$S/gocache CC=gcc
mkdir -p $S/realout/lean; ln -s realout $S/out
cp -r $W/ffi/go-example $S/go-example; rm -f $S/go-example/gofig*
for f in $W/ffi/audit/out/lean/*.a; do ln -s $f $S/realout/lean/; done
P=$(lean --print-prefix)
O=$W/.lake/build/ir/Figram; OBJS=""; for m in Core Chart Compile Lint Format FormatIO Engine Api; do OBJS="$OBJS $O/$m.c.o.export"; done
git -C $W show d69bbaf:ffi/figram_ffi.c > $S/ffi_baseline.c
leanc -c -o $S/ffi_baseline.o $S/ffi_baseline.c 2>/dev/null
ar rcs $S/lib_baseline.a $S/ffi_baseline.o $OBJS
rm -f $S/realout/libfigram.a; ar rcs $S/realout/libfigram.a $S/ffi_baseline.o $OBJS
cd $S/go-example
go build -o gofig1 . ; go test -c -o t1.test .
echo "1) built against baseline lib: gofig1=$(nm gofig1 | grep -oE ' b (ready|init_state)$')  t1.test=$(nm t1.test | grep -oE ' b (ready|init_state)$')"
rm -f $S/realout/libfigram.a; ar rcs $S/realout/libfigram.a $W/ffi/audit/out/figram_ffi.o $OBJS   # пересборка как в build.sh: rm + ar
echo "   lib now has: $(nm $S/out/libfigram.a 2>/dev/null | grep -oE ' b (ready|init_state)$')"
go build -o gofig2 . ; go test -c -o t2.test .
echo "2) rebuilt after lib change:   gofig2=$(nm gofig2 | grep -oE ' b (ready|init_state)$')  t2.test=$(nm t2.test | grep -oE ' b (ready|init_state)$')"
go build -a -o gofig3 .
echo "3) go build -a:                gofig3=$(nm gofig3 | grep -oE ' b (ready|init_state)$')"
rm -rf $S

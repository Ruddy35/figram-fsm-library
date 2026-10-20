#!/usr/bin/env bash
# Воспроизведение находок Go-обёртки на BASELINE (d69bbaf: main.go и figram_ffi.c) с тестами аудита.
#   REPRODUCTIONS/repro_go_baseline.sh [WORKTREE]   (нужны env.sh и ffi/audit/build_audit.sh в WORKTREE)
set -u
W=${1:-/home/user/figram-audit-worktrees/ffi-go}
S=$(mktemp -d); trap 'rm -rf $S' EXIT
export GOCACHE=$S/gocache CC=gcc GOMAXPROCS=4
O=$W/.lake/build/ir/Figram; OBJS=""; for m in Core Chart Compile Lint Format FormatIO Engine Api; do OBJS="$OBJS $O/$m.c.o.export"; done
mkdir -p $S/ffi/out/lean $S/ffi/go-example
for f in $W/ffi/audit/out/lean/*.a; do ln -s $f $S/ffi/out/lean/; done
git -C "$W" show d69bbaf:ffi/figram_ffi.c > $S/ffi.c; leanc -c -o $S/ffi.o $S/ffi.c 2>/dev/null
ar rcs $S/ffi/out/libfigram.a $S/ffi.o $OBJS
for f in go.mod init_helper.go main.go race_test.go; do git -C "$W" show d69bbaf:ffi/go-example/$f > $S/ffi/go-example/$f; done
cp $W/ffi/go-example/audit_test.go $S/ffi/go-example/
mkdir -p $S/examples; cp $W/examples/pizza.bot.json $S/examples/
cd $S/ffi/go-example && go test -c -o t.test . || exit 2
echo "== FFI-11 op не строка → panic; FFI-08 NUL в Load принят; FFI-12 hello всегда ошибка (ожидается FAIL)"
./t.test -test.run 'TestAuditRequestBadOp$|TestAuditLoadNUL$|TestAuditHelloAlwaysError$' -test.v 2>&1 | grep -E "audit_test|^--- "
echo "== FFI-01 в Go: 8 горутин одновременно вызывают figram_init (ожидается часть процессов SIGSEGV)"
FIGRAM_INIT_RACE=50 ./t.test -test.run 'TestAuditInitRace$' -test.v 2>&1 | grep -E "concurrent init|^--- "

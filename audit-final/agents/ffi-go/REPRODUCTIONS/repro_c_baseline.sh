#!/usr/bin/env bash
# Воспроизведение находок C-уровня на BASELINE-прослойке (d69bbaf:ffi/figram_ffi.c).
# Одна команда:  REPRODUCTIONS/repro_c_baseline.sh [WORKTREE]
# Нужны: source /home/user/figram-audit/env.sh; в WORKTREE выполнен ffi/audit/build_audit.sh
# (из него берутся объекты модулей Lean и статические библиотеки рантайма).
set -u
W=${1:-/home/user/figram-audit-worktrees/ffi-go}
S=$(mktemp -d); trap 'rm -rf $S' EXIT
P=$(lean --print-prefix); B=$W/ffi/audit/out; D=$W/examples/pizza.bot.json
O=$W/.lake/build/ir/Figram; OBJS=""; for m in Core Chart Compile Lint Format FormatIO Engine Api; do OBJS="$OBJS $O/$m.c.o.export"; done
git -C "$W" show d69bbaf:ffi/figram_ffi.c > $S/figram_ffi.c
leanc -c -o $S/ffi.o $S/figram_ffi.c 2>/dev/null
LIBS="$S/ffi.o $OBJS -L$B/lean -Wl,--start-group -lInit -lleanrt -Wl,--end-group -Wl,-Bstatic -lstdc++ -luv -lgmp -Wl,-Bdynamic -lm -ldl -lpthread -lrt"
gcc -O1 -g -I$P/include -pthread -o $S/contract $W/ffi/audit/contract.c $LIBS 2>/dev/null
gcc -O1 -g -pthread -Wl,--wrap=malloc -o $S/oom $W/ffi/audit/oom.c $LIBS
r() { echo "\$ $*"; timeout 60 "$@" > $S/o 2>&1; rc=$?; grep -v "Segmentation fault" $S/o | head -6; echo "  -> exit $rc"; }
echo "== FFI-01 одновременный figram_init (ожидается: часть процессов SIGSEGV=139)"
r "$W/ffi/audit/init_race.sh" $S/contract 300
echo "== FFI-05 отказ malloc в dup (ожидается 139)"; r $S/oom
echo "== FFI-06 figram_unload(NULL) после неудачной загрузки (ожидается 139)"; r $S/contract null unload_failed_load $D
echo "== FFI-03 вызов до figram_init (load+request: 139)"; r $S/contract before_init load $D
echo "== FFI-07 use-after-unload: ответ меняется молча (exit 0)"; r $S/contract use_after_unload $D $W/examples/features.bot.json
echo "== FFI-07 двойная выгрузка (UB: молча exit 0 или SIGSEGV 139, недетерминированно)"; r $S/contract unload_twice $D
echo "== FFI-08 NUL в документе: хвост игнорируется; NUL в тексте ошибки"; r $S/contract nul $D
echo "== FFI-09 побочные эффекты figram_init"; r $S/contract side_effects
echo "== FFI-10 figram_init при RLIMIT_AS=1.5 ГБ (ожидается abort=134 вместо кода 1)"; r prlimit --as=1500000000 -- $S/contract init_twice

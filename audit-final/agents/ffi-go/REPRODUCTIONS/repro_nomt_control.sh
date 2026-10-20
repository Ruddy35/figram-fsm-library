#!/usr/bin/env bash
# Контроль чувствительности (TEST-находка FFI-15): мутант прослойки БЕЗ lean_mark_mt.
# Функциональная нагрузка 16 потоков на один автомат даёт расхождения, а TSan
# (lean.h под TSan делает ST-счётчики атомарными seq_cst) — 0 предупреждений.
#   REPRODUCTIONS/repro_nomt_control.sh [WORKTREE]   (нужны env.sh и ffi/audit/build_audit.sh)
set -u
W=${1:-/home/user/figram-audit-worktrees/ffi-go}
S=$(mktemp -d); trap 'rm -rf $S' EXIT
P=$(lean --print-prefix); B=$W/ffi/audit/out
O=$W/.lake/build/ir/Figram; OBJS=""; TS=""; for m in Core Chart Compile Lint Format FormatIO Engine Api; do OBJS="$OBJS $O/$m.c.o.export"; TS="$TS $B/tsan/$m.o"; done
RT="-L$B/lean -Wl,--start-group -lInit -lleanrt -Wl,--end-group -Wl,-Bstatic -lstdc++ -luv -lgmp -Wl,-Bdynamic -lm -ldl -lpthread -lrt"
sed 's/  lean_mark_mt(m);/  \/* MUTANT *\//' $W/ffi/figram_ffi.c > $S/ffi_nomt.c
grep -c MUTANT $S/ffi_nomt.c
leanc -c -o $S/p.o $S/ffi_nomt.c 2>/dev/null
gcc -O1 -pthread -o $S/stress_nomt $W/ffi/audit/stress.c $S/p.o $OBJS $RT
gcc -c -fsanitize=thread -g -O1 -I$P/include -o $S/t.o $S/ffi_nomt.c
gcc -O1 -g -pthread -fsanitize=thread -o $S/stress_nomt_tsan $W/ffi/audit/stress.c $S/t.o $TS $RT
echo "== plain, без lean_mark_mt (ожидается: расхождения, код 1)"
$S/stress_nomt $W/examples/pizza.bot.json 16 20000 0 2>/dev/null; echo "  -> exit $?"
echo "== TSan, без lean_mark_mt: число предупреждений TSan"
TSAN_OPTIONS="halt_on_error=0 report_signal_unsafe=0" $S/stress_nomt_tsan $W/examples/pizza.bot.json 16 3000 0 2> $S/tsan.err; echo "  -> exit $?"
echo "  ThreadSanitizer warnings: $(grep -c 'WARNING: ThreadSanitizer' $S/tsan.err)"

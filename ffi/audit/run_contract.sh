#!/usr/bin/env bash
# Прогон подтестов contract (каждый в своём процессе) с фиксацией кода выхода/сигнала.
#   ffi/audit/run_contract.sh BIN_DIR [suffix]   (suffix: "" | _asan | _tsan)
set -u
cd "$(dirname "$0")/../.."
B=${1:-ffi/audit/out}; S=${2:-}
D=examples/pizza.bot.json; D2=examples/features.bot.json
C="$B/contract$S"
run() {
  local name=$1; shift
  out=$(ASAN_OPTIONS=detect_leaks=0 UBSAN_OPTIONS=print_stacktrace=1 timeout 60 "$@" 2>&1); rc=$?
  sig=""; [ $rc -gt 128 ] && sig=" (signal $((rc-128)))"
  printf '=== %-34s rc=%d%s\n' "$name" $rc "$sig"
  printf '%s\n' "$out" | grep -v '^\s*$' | head -${LINES_SHOWN:-12} | sed 's/^/    /'
}
run init_twice            $C init_twice
run side_effects          $C side_effects
for w in check fmt load; do run "before_init $w" $C before_init $w $D; done
for w in check fmt load line handle unload free unload_failed_load; do run "null $w" $C null $w $D; done
run unload_twice          $C unload_twice $D
run use_after_unload      $C use_after_unload $D $D2
run nul                   $C nul $D
run utf8                  $C utf8 $D
run "threads 1000"        $C threads $D 1000

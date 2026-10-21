#!/usr/bin/env bash
# Regression suite of the C ABI (docs/testing.md); each subtest runs in its own process.
# Exit code 0 only if every check passes.
#   tests/capi/regress.sh [BIN_DIR]   (run tests/capi/build.sh first)
set -u
B=${1:-}; case $B in ""|/*) ;; *) B=$PWD/$B;; esac   # BIN_DIR is relative to the caller's directory
cd "$(dirname "$0")/../.."
B=${B:-tests/capi/out}; D=examples/pizza.bot.json; D2=examples/features.bot.json; fail=0; n=0; nfail=0
check() {  # name expected_code command…
  local name=$1 want=$2; shift 2
  out=$(timeout 120 "$@" 2>&1); rc=$?; n=$((n+1))
  if [ "$rc" -eq "$want" ]; then echo "PASS $name (rc=$rc)"
  else
    nfail=$((nfail+1))
    echo "FAIL $name: rc=$rc, want $want"
    printf '%s\n' "$out" | grep -E "^FAIL|terminate|what\(\)" | head -12 | sed 's/^/    /'
    printf '%s\n' "$out" | tail -2 | sed 's/^/    /'; fail=1
  fi
}
# check_out name regex command… — exit code 0 AND an output line matches the regular expression.
check_out() {
  local name=$1 rx=$2; shift 2
  out=$(timeout 120 "$@" 2>&1); rc=$?; n=$((n+1))
  if [ "$rc" -eq 0 ] && printf '%s\n' "$out" | grep -qE "$rx"; then echo "PASS $name (rc=0, matches '$rx')"
  else
    nfail=$((nfail+1))
    echo "FAIL $name: rc=$rc, want 0 and a line matching '$rx'"
    printf '%s\n' "$out" | grep -E "^FAIL|terminate|what\(\)|figram_init=" | head -12 | sed 's/^/    /'; fail=1
  fi
}
# check_rc_out name code regex command… — the given exit code AND a line matching the regular expression.
check_rc_out() {
  local name=$1 want=$2 rx=$3; shift 3
  out=$(timeout 120 "$@" 2>&1); rc=$?; n=$((n+1))
  if [ "$rc" -eq "$want" ] && printf '%s\n' "$out" | grep -qE "$rx"; then echo "PASS $name (rc=$rc, matches '$rx')"
  else
    nfail=$((nfail+1))
    echo "FAIL $name: rc=$rc, want $want and a line matching '$rx'"
    printf '%s\n' "$out" | tail -3 | sed 's/^/    /'; fail=1
  fi
}
# Concurrent figram_init from two threads (300 fresh processes).
check "concurrent figram_init, 2 threads x 300 processes" 0 tests/capi/init_race.sh "$B/contract" 300
# A failed malloc of a result string: NULL instead of SIGSEGV.
check "malloc failure when copying a result" 0 "$B/oom"
# figram_unload(NULL) is like free(NULL), also after a failed load.
check "figram_unload(NULL)" 0 "$B/contract" null unload $D
check "unload(failed load)" 0 "$B/contract" null unload_failed_load $D
# The Lean runtime starts under RLIMIT_AS: the mimalloc arena is not reserved in advance
# (ffi/figram_rt.c) and the runtime creates no threads (ffi/figram_init.cpp).
for h in 2 4 8 16 32 48 64 96 128 256 1024; do
  check_out "init with $h MiB address-space headroom → rc 0" '^figram_init=0 ' \
    env FIGRAM_TEST_AS_HEADROOM=$((h << 20)) "$B/contract" rlimit_init
done
check_out "init under RLIMIT_AS=512MB → rc 0" '^figram_init=0 ' prlimit --as=512000000 -- "$B/contract" rlimit_init
# The boundary: with too little free address space the Lean allocator ends the process with its own
# out-of-memory message (exit code 1), as when memory runs out later, without a signal or a hang.
check_rc_out "init with 256 KiB headroom → Lean out-of-memory exit 1, no signal" 1 'INTERNAL PANIC: out of memory' \
  env FIGRAM_TEST_AS_HEADROOM=262144 "$B/contract" rlimit_init
# Calls before figram_init: lazy thread-safe initialization.
check "lazy init (load/request before figram_init)" 0 "$B/contract" lazy $D
check "lazy init race, 8 threads x 300 processes" 0 tests/capi/init_race.sh "$B/contract" 300 lazy_race
# NULL, a foreign pointer, use after unload, double unload.
check "NULL arguments and foreign handle" 0 "$B/contract" null_safe $D
for w in check fmt load line handle; do check "null $w: no SIGSEGV" 0 "$B/contract" null $w $D; done
check "use-after-unload / double unload detected" 0 "$B/contract" lifetime $D $D2
# The length variants, load_doc_ex with a reason, NUL in an error text.
check "length variants, load_doc_ex, NUL in output" 0 "$B/contract" lengths $D
# Invalid UTF-8: parse_error / the document is rejected.
check "strict UTF-8 on the C ABI path" 0 "$B/contract" utf8_strict $D
# The request size limit (too_large), an empty request (parse_error).
check "request size limit and empty request" 0 "$B/contract" limit $D
# The host's SIGPIPE, SIGSEGV, SIGBUS dispositions and the calling thread's sigaltstack are
# preserved; the embedded runtime starts no threads and opens no descriptors.
check "signals preserved (defaults)" 0 "$B/contract" signals default
check "signals preserved (custom handlers + altstack)" 0 "$B/contract" signals custom
check "no runtime threads or file descriptors" 0 "$B/contract" host_state $D
# The main harness and the stress test with loads and unloads of other machines.
check "harness 16x20000 + reload" 0 "$B/harness_plain" $D memory
check "stress 16x20000 + 4 loaders" 0 "$B/stress" $D 16 20000 4
echo "C ABI regression checks: $n, failed: $nfail"
exit $fail

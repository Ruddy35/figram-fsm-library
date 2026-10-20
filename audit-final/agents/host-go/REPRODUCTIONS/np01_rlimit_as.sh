#!/usr/bin/env bash
# NP-01: figram_init aborts the WHOLE embedding process (uncaught C++ lean::exception
# "failed to create thread" -> std::terminate -> SIGABRT) when the Lean runtime cannot
# start its libuv event-loop thread, e.g. under RLIMIT_AS = 3 GB. 4 GB works.
B=/home/user/figram-audit-worktrees/host-go/audit/host-go/bin
for as in 4000000000 3000000000; do
  prlimit --as=$as -- "$B/fgmem" -reloads 10 -reqs 5 -goroutines 2 > /dev/null 2> /tmp/np01.$as.err
  echo "RLIMIT_AS=$as rc=$?"; grep -m3 -E "terminate|what\(\)|SIGABRT|_Cfunc_fg_init" /tmp/np01.$as.err
done

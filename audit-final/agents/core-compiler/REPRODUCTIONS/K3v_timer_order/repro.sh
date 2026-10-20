#!/bin/sh
# FORMAT.md К3в says two documents that differ only in dictionary key order "behave the same".
# The order of keys in a message's "timers" is observable: it fixes the order of
# start_timer/cancel_timer actions (and thus the outbox keys user:seq:N of HOST.md §1.4).
ENGINE=${1:-/home/user/figram-audit-worktrees/core-compiler/bin/figram-engine}
cd "$(dirname "$0")"
for d in ab ba; do printf '%s\n' '{"id":1,"op":"start"}' | "$ENGINE" $d.bot.json; done

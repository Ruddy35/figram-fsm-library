#!/usr/bin/env bash
# NP-02: what figram_init does to the embedding (Go) process: threads (libuv loop +
# io_uring SQPOLL kernel thread), +2.2 GB virtual size, sigaltstack of the calling M.
B=/home/user/figram-audit-worktrees/host-go/audit/host-go/bin
"$B/fgprobe" /home/user/figram-audit/hosts/shared/bots/pizza.bot.json | head -3
# signal storm on the M that ran figram_init, Lean's 14.5 KiB alt stack left in place
FIGRAM_KEEP_LEAN_SIGALTSTACK=1 "$B/fgsigprobe" -secs 10; echo "rc=$?"

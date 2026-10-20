#!/bin/sh
# Minimal shrunk form of H2 (found by diff_random.py with the designer reading
# --oracle-opt timer_accept=owned --oracle-opt exit_cancels=snapshot, reduced by shrink.py):
# one message without timers, snapshot carries a stale timer t2; leaving the message
# (self arrow) neither cancels nor drops it. Expected (designer): cancel_timer t2.
ENGINE=${1:-/home/user/figram-audit-worktrees/core-compiler/bin/figram-engine}
cd "$(dirname "$0")"
printf '%s\n' '{"id":1,"op":"step","snapshot":{"at":"m2","timers":{"t2":32},"seq":1},"event":"btn:b"}' | "$ENGINE" bot.json

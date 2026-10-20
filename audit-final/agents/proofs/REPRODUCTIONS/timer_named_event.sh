#!/usr/bin/env bash
# Decision 7 at the protocol level: a 'timer:' event without token is refused by the engine
# (Engine.decEvent), a stale token is ignored, the current token fires. In the Core MODEL the same
# key as a named event fires without any token (audit/proofs/Counterexamples.lean, CE2).
# Usage: timer_named_event.sh [figram-engine] [features.bot.json]. Exit 0 = all three as expected.
set -u
E=${1:-/home/user/figram-audit/fresh/figram-engine}
D=${2:-/home/user/figram-audit-worktrees/proofs/examples/features.bot.json}
out=$(printf '%s\n' \
 '{"id":1,"op":"step","snapshot":{"at":"item","timers":{"remind":3},"seq":3},"event":"timer:remind"}' \
 '{"id":2,"op":"step","snapshot":{"at":"item","timers":{"remind":3},"seq":3},"event":"timer:remind","token":2}' \
 '{"id":3,"op":"step","snapshot":{"at":"item","timers":{"remind":3},"seq":3},"event":"timer:remind","token":3}' \
 | timeout 10 "$E" "$D")
echo "$out"
echo "$out" | sed -n 1p | grep -q '"error"' && \
echo "$out" | sed -n 2p | grep -q '"actions":\[\]' && \
echo "$out" | sed -n 3p | grep -q '"send":"reminder"'

#!/bin/sh
# H3d: message m has an UNCONDITIONAL variant for btn:y; the enclosing section's guarded
# variant (if c1) can never win from m, yet the engine asks the host to evaluate c1.
ENGINE=${1:-/home/user/figram-audit-worktrees/core-compiler/bin/figram-engine}
cd "$(dirname "$0")"
printf '%s\n' '{"id":1,"op":"step","snapshot":{"at":"m","seq":1},"event":"btn:y"}' \
              '{"id":2,"op":"step","snapshot":{"at":"m","seq":1},"event":"btn:y","guards":{"c1":true}}' \
              '{"id":3,"op":"step","snapshot":{"at":"m","seq":1},"event":"btn:y","guards":{"c1":false}}' | "$ENGINE" bot.json
# observed: id 1 -> {"need":["c1"]}; ids 2 and 3 give the identical result (at A): c1 is irrelevant.

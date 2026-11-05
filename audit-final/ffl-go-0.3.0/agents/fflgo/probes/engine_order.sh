#!/bin/sh
# Engine probes: map order in snapshots, cancel_timer order on leaving/restart/recovery, recovery of a stale outcome.
E=/home/user/figram-audit-worktrees/cleanup-int/.lake/build/bin/figram-engine
D=/home/user/figram-audit/agents7/fflgo/docs/order.bot.json
P=/home/user/figram-audit-worktrees/cleanup-int/examples/pizza.bot.json
printf '%s\n' '{"id":1,"op":"start"}' \
 '{"id":11,"op":"step","snapshot":{"at":"home","timers":{"old2":1,"old":1},"seq":1},"event":"btn:go"}' \
 '{"id":12,"op":"step","snapshot":{"at":"home","timers":{"old":1,"old2":1},"seq":1},"event":"btn:go"}' \
 '{"id":21,"op":"step","snapshot":{"at":"gone","timers":{"zeta":1,"alpha":1},"seq":1},"event":"btn:nothing"}' \
 '{"id":22,"op":"step","snapshot":{"at":"gone","timers":{"alpha":1,"zeta":1},"seq":1},"event":"btn:nothing"}' \
 '{"id":31,"op":"restart","snapshot":{"at":"home","timers":{"mid":1,"alpha":1,"zeta":1},"seq":1}}' \
 '{"id":32,"op":"restart","snapshot":{"at":"home","timers":{"alpha":1,"mid":1,"zeta":1},"seq":1}}' \
 | $E $D
printf '%s\n' \
 '{"id":41,"op":"step","snapshot":{"at":"placing","resume":{"order":"placing"},"seq":6},"event":"call:create_order:created","token":6}' \
 '{"id":42,"op":"step","snapshot":{"at":"gone","resume":{"order":"placing"},"seq":6},"event":"call:create_order:created","token":6}' \
 '{"id":43,"op":"step","snapshot":{"at":"cart","timers":{"cart_reminder":3},"seq":3},"event":"timer:cart_reminder","token":2}' \
 '{"id":44,"op":"step","snapshot":{"at":"gone","timers":{"cart_reminder":3},"seq":3},"event":"timer:cart_reminder","token":2}' \
 | $E $P

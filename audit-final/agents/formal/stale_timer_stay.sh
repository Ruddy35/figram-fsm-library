#!/usr/bin/env bash
# host-contract.md §6: "the old timer's entry does not survive the first transition".
# With 0.3.0 the first transition can be an arrow without a target (btn:log on `home`), which
# leaves the message neither left nor entered: the renamed timer's entry survives it.
cd /home/user/figram-audit-worktrees/a5-formal
printf '%s\n' \
 '{"id":1,"op":"step","snapshot":{"at":"home","timers":{"idle":1,"old_name":1},"seq":1},"event":"btn:log"}' \
 '{"id":2,"op":"step","snapshot":{"at":"home","timers":{"idle":1,"old_name":1},"seq":1},"event":"btn:self"}' \
 | timeout 20 .lake/build/bin/figram-engine examples/features.bot.json

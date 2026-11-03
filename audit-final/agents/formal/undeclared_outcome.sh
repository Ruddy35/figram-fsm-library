#!/usr/bin/env bash
# An outcome name the action does not declare is accepted by the engine: the wait is removed.
cd /home/user/figram-audit-worktrees/a5-formal
printf '%s\n' \
 '{"id":1,"op":"step","snapshot":{"at":"placing","resume":{"order":"placing"},"pending":[{"call":"create_order","token":6}],"seq":6},"event":"call:create_order:Created","token":6}' \
 '{"id":2,"op":"step","snapshot":{"at":"placing","resume":{"order":"placing"},"pending":[{"call":"create_order","token":6}],"seq":6},"event":"call:clear_cart:created","token":6}' \
 | timeout 20 .lake/build/bin/figram-engine examples/pizza.bot.json

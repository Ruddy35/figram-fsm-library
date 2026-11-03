#!/usr/bin/env bash
# The snapshot decoder accepts a wait whose token exceeds `seq` (not written by any engine):
# `Fresh` (tokens <= seq), the hypothesis behind "each call is answered once", is not checked.
cd /home/user/figram-audit-worktrees/a5-formal
printf '%s\n' \
 '{"id":1,"op":"step","snapshot":{"at":"home","pending":[{"call":"ping","token":9}],"seq":8},"event":"call:ping:failed","token":9}' \
 '{"id":2,"op":"step","snapshot":{"at":"home","seq":8},"event":"btn:ping"}' \
 | timeout 20 .lake/build/bin/figram-engine examples/features.bot.json

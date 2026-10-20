#!/usr/bin/env bash
# FC-04: requests are not validated for unknown fields: a misspelt "guards" is ignored
# (endless need), junk on start/hello is ignored, but a non-bool value of an unused guard is an error.
E="${1:-/home/user/figram-audit-worktrees/formats-correctness/bin/figram-engine}"
DOC=/home/user/figram-audit-worktrees/formats-correctness/examples/features.bot.json
printf '%s\n' \
 '{"id":1,"op":"step","snapshot":{"at":"home"},"event":"btn:go","gaurds":{"is_vip":true}}' \
 '{"id":2,"op":"step","snapshot":{"at":"home"},"event":"btn:go","gaurds":{"is_vip":true}}' \
 '{"id":3,"op":"start","snapshot":null,"guards":5,"event":7}' \
 '{"id":4,"op":"step","snapshot":{"at":"home"},"event":"btn:self","guards":{"unused":1}}' | "$E" "$DOC"

#!/usr/bin/env bash
# A user has three `every` waits of `award` (points.bot.json). The document is reloaded with
# `award` declared without `every` (latest). The next call of `award` removes all three older
# waits at once; their outcomes are then ignored (the credit for them is lost).
E=/home/user/figram-audit-worktrees/a6-hostgo/.lake/build/bin/figram-engine
B=/home/user/figram-audit/agents6/hostgo/bots
S='{"at":"home","pending":[{"call":"award","token":3},{"call":"award","token":2},{"call":"award","token":1}],"seq":3}'
printf '%s\n' "{\"id\":1,\"op\":\"step\",\"snapshot\":$S,\"event\":\"btn:earn\"}" \
  "{\"id\":2,\"op\":\"step\",\"snapshot\":{\"at\":\"home\",\"pending\":[{\"call\":\"award\",\"token\":4}],\"seq\":4},\"event\":\"call:award:ok\",\"token\":2}" \
  "{\"id\":3,\"op\":\"step\",\"snapshot\":$S,\"event\":\"call:award:ok\",\"token\":2}" | $E $B/points.latest.bot.json

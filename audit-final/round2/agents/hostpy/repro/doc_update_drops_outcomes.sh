#!/usr/bin/env bash
# host-contract section 6: "a wait of an action that ... loses its outcomes in the new version still
# accepts its outcome, and the outcome then only removes the wait". A user waiting on `placing`
# (shop v1) when v2 makes create_order synchronous stays on `placing` for good; figram check
# of v2 reports nothing about the users already waiting there.
set -u
REPO=${FIGRAM_REPO:-/home/user/figram-audit-worktrees/a6-hostpy}
E=$REPO/.lake/build/bin/figram-engine
D=$(dirname "$0")/../bots
echo "== figram check v2"; $REPO/.lake/build/bin/figram check $D/shop_v2_sync.bot.json; echo "check rc=$?"
s='{"at":"placing","resume":{"shop":"confirm"},"back":"confirm","timers":{"slow":6},"pending":[{"call":"create_order","token":6}],"seq":6}'
out=$(printf '%s\n' "{\"id\":1,\"op\":\"step\",\"snapshot\":$s,\"event\":\"call:create_order:created\",\"token\":6}" \
  "{\"id\":2,\"op\":\"step\",\"snapshot\":$s,\"event\":\"timer:slow\",\"token\":6}" | $E $D/shop_v2_sync.bot.json)
echo "$out"
python3 - "$out" <<'PY'
import json, sys
r = json.loads(sys.argv[1].splitlines()[0])["snapshot"]
print("RESULT after the outcome:", r)
sys.exit(1 if r["at"] == "placing" and not r.get("pending") else 0)
PY

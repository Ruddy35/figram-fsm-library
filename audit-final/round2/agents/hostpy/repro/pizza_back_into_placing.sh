#!/usr/bin/env bash
# A user waiting on pizza's "placing" opens /help; the outcome of create_order arrives while the
# user is on help (no arrow there: it only removes the wait); "back" then returns the user to
# "placing", a "please wait" message that nobody will ever answer. figram check: ok, no warning.
set -u
REPO=${FIGRAM_REPO:-/home/user/figram-audit-worktrees/a6-hostpy}
E=$REPO/.lake/build/bin/figram-engine
DOC=$REPO/examples/pizza.bot.json
echo "== figram check"; $REPO/.lake/build/bin/figram check $DOC; echo "check rc=$?"
s1='{"at":"pay","resume":{"order":"pay"},"seq":5}'
r1=$(printf '%s\n' "{\"id\":1,\"op\":\"step\",\"snapshot\":$s1,\"event\":\"payment:success\"}" | $E $DOC); echo "$r1"
s2=$(python3 -c 'import json,sys; print(json.dumps(json.loads(sys.argv[1])["snapshot"]))' "$r1")
r2=$(printf '%s\n' "{\"id\":2,\"op\":\"step\",\"snapshot\":$s2,\"event\":\"cmd:help\"}" | $E $DOC); echo "$r2"
s3=$(python3 -c 'import json,sys; print(json.dumps(json.loads(sys.argv[1])["snapshot"]))' "$r2")
r3=$(printf '%s\n' "{\"id\":3,\"op\":\"step\",\"snapshot\":$s3,\"event\":\"call:create_order:created\",\"token\":6}" | $E $DOC); echo "$r3"
s4=$(python3 -c 'import json,sys; print(json.dumps(json.loads(sys.argv[1])["snapshot"]))' "$r3")
r4=$(printf '%s\n' "{\"id\":4,\"op\":\"step\",\"snapshot\":$s4,\"event\":\"btn:back\"}" | $E $DOC); echo "$r4"
python3 - "$r4" <<'PY'
import json, sys
s = json.loads(sys.argv[1])["snapshot"]
stuck = s["at"] == "placing" and not s.get("pending")
print("RESULT: user on", s["at"], "pending", s.get("pending"), "->", "STUCK (no wait, nobody will answer)" if stuck else "not stuck")
sys.exit(1 if stuck else 0)
PY

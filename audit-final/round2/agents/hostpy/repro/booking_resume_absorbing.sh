#!/usr/bin/env bash
# bots/booking.bot.json (figram check: ok). A user opened /help while waiting on `confirming`;
# the outcome `full` (arrow only on `confirming`, like pizza's `rejected`) arrived on `help` and
# only removed the wait. From then on "book" resumes into `confirming` (the remembered place of
# the section), `cancel` leaves the remembered place unchanged, and "book" leads there again:
# the user can never book again.
set -u
REPO=${FIGRAM_REPO:-/home/user/figram-audit-worktrees/a6-hostpy}
E=$REPO/.lake/build/bin/figram-engine
DOC=$(dirname "$0")/../bots/booking.bot.json
echo "== figram check"; $REPO/.lake/build/bin/figram check $DOC; echo "check rc=$?"
python3 - "$E" "$DOC" <<'PY'
import json, subprocess, sys
E, DOC = sys.argv[1:]
p = subprocess.Popen([E, DOC], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
n = 0
def req(r):
    global n; n += 1
    p.stdin.write(json.dumps({"id": n, **r}) + "\n"); p.stdin.flush()
    out = json.loads(p.stdout.readline()); print(json.dumps(r)[:110], "->", json.dumps(out)[:200]); return out
s = {"at": "holding", "resume": {"booking": "holding"}, "back": "start", "seq": 3, "timers": {"hold": 3}}
s = req({"op": "step", "snapshot": s, "event": "btn:confirm", "guards": {"seat_chosen": True}})["snapshot"]
tok = s["pending"][0]["token"]
s = req({"op": "step", "snapshot": s, "event": "cmd:help"})["snapshot"]
s = req({"op": "step", "snapshot": s, "event": "call:book_seat:full", "token": tok})["snapshot"]
s = req({"op": "step", "snapshot": s, "event": "btn:back"})["snapshot"]
stuck = 0
for i in range(3):
    s = req({"op": "step", "snapshot": s, "event": "btn:cancel"})["snapshot"]
    s = req({"op": "step", "snapshot": s, "event": "btn:book"})["snapshot"]
    stuck += s["at"] == "confirming" and not s.get("pending")
print("RESULT: after 3 x (cancel, book) the user is on", s["at"], "pending", s.get("pending"), "stuck times", stuck)
sys.exit(1 if stuck == 3 else 0)
PY

#!/usr/bin/env python3
"""When does the "please wait" message of a sequential action with outcomes reach the user?
For every step whose reply is [call X (token), …, send W] it compares the time the call finished,
the time W was sent and the time the message chosen by X's outcome was sent."""
import json, sqlite3, statistics, sys
run = sys.argv[1]
H = sqlite3.connect(f"file:{run}/host.db?mode=ro", uri=True)
rows = H.execute("SELECT id, user, seq, pos, kind, name, token, run, created, executed_at FROM outbox ORDER BY id").fetchall()
by_step = {}
for r in rows:
    by_step.setdefault((r[1], r[2], r[8]), []).append(r)
after_call, gap_wait_to_next, call_ms, total = [], [], [], 0
for (user, seq, created), acts in by_step.items():
    calls = [a for a in acts if a[4] == "call" and a[6] is not None and a[5] in ("create_order", "book_seat")]
    sends = [a for a in acts if a[4] == "send"]
    if not calls or not sends:
        continue
    c, s = calls[0], sends[-1]
    if c[9] is None or s[9] is None:
        continue
    total += 1
    after_call.append(s[9] >= c[9])
    call_ms.append((c[9] - created) * 1000)
    # the outcome's step: the next send of the same user after s
    nxt = H.execute("SELECT MIN(executed_at) FROM outbox WHERE user=? AND kind='send' AND id>? AND executed_at IS NOT NULL",
                    (user, s[0])).fetchone()[0]
    if nxt:
        gap_wait_to_next.append((nxt - s[9]) * 1000)
q = lambda xs, p: sorted(xs)[min(len(xs) - 1, int(p * len(xs)))] if xs else None
print(json.dumps({
    "steps_with_call_and_wait_message": total,
    "wait_message_sent_after_the_call_finished": sum(after_call),
    "commit_to_call_finished_ms": {"p50": q(call_ms, .5), "p90": q(call_ms, .9), "max": max(call_ms) if call_ms else None},
    "wait_message_to_next_message_ms": {"p50": q(gap_wait_to_next, .5), "p90": q(gap_wait_to_next, .9)},
}, indent=1))

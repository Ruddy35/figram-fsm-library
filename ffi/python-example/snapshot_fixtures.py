"""Схема снимка и движок принимают и отвергают одни и те же снимки (N03).

    python3 snapshot_fixtures.py ENGINE bot.json snapshot.schema.json
"""

import json
import subprocess
import sys

import jsonschema

from protocol import ChildTimeout, ProtocolError, check_reply, default_timeout, run_child

M = 9007199254740991
FIXTURES = [
    {"at": "home"},
    {"at": "home", "v": 1, "seq": 0},
    {"at": "home", "seq": M},
    {"at": "home", "seq": M + 1},
    {"at": "home", "seq": -1},
    {"at": "home", "seq": 1.5},
    {"at": "home", "seq": "1"},
    {"at": ""},
    {"at": 5},
    {},
    {"at": "home", "v": 2},
    {"at": "home", "extra": 1},
    {"at": "home", "resume": {"shop": "item"}},
    {"at": "home", "resume": {"shop": ""}},
    {"at": "home", "resume": {"": "item"}},
    {"at": "home", "resume": {"shop": 3}},
    {"at": "home", "resume": []},
    {"at": "home", "timers": {"idle": 0}},
    {"at": "home", "timers": {"idle": M}},
    {"at": "home", "timers": {"idle": M + 1}},
    {"at": "home", "timers": {"idle": -1}},
    {"at": "home", "timers": {"": 1}},
    {"at": "home", "timers": {"idle": "1"}},
    [],
]

engine, doc, schema_path = sys.argv[1:4]
validator = jsonschema.Draft202012Validator(json.load(open(schema_path)))


def ask(requests, phase):
    """Отправить запросы; вернуть ответы, проверенные по протоколу и id.
    Любое расхождение в числе, id или форме ответа — провал."""
    data = "\n".join(json.dumps({**r, "id": i}) for i, r in enumerate(requests)) + "\n"
    try:
        out = run_child([engine, doc], data.encode(), default_timeout(), phase)
    except ChildTimeout as e:
        raise SystemExit(str(e))
    lines = out.stdout.decode().split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    if out.returncode != 0 or len(lines) != len(requests):
        raise SystemExit(f"{phase}: ответов {len(lines)} из {len(requests)}, код {out.returncode}")
    replies = []
    for i, (r, line) in enumerate(zip(requests, lines)):
        try:
            resp = json.loads(line)
            variant = check_reply(resp, i, r["op"])
        except (ValueError, ProtocolError) as e:
            raise SystemExit(f"{phase}: ответ {i} нарушает протокол: {e}")
        replies.append((variant, resp))
    return replies


replies = ask([{"op": "step", "snapshot": s, "event": "unknown:event"} for s in FIXTURES],
              "граничные снимки")
bad = 0
for snap, (variant, resp) in zip(FIXTURES, replies):
    by_engine = variant == "result"
    by_schema = validator.is_valid(snap)
    if by_engine != by_schema:
        bad += 1
        print(f"  расходятся: {snap}: схема {by_schema}, движок {by_engine} {resp.get('error', '')}")
    elif not by_engine and resp.get("code") != "bad_snapshot":
        # отвергнутый снимок — именно код bad_snapshot (FORMAT.md §7, «Ошибки»)
        bad += 1
        print(f"  расходятся: {snap}: код ошибки {resp.get('code')!r} вместо 'bad_snapshot'")
print(f"снимков {len(FIXTURES)}, ответов {len(replies)}, расхождений схемы и движка {bad}")

# N06: на границе seq движок не выдаёт снимок, который сам не прочитает.
edge = ask([
    {"op": "step", "snapshot": {"at": "home", "seq": M}, "event": "btn:self"},
    {"op": "step", "snapshot": {"at": "home", "seq": M}, "event": "unknown:event"},
    {"op": "step", "snapshot": {"at": "home", "seq": M - 1}, "event": "btn:self"},
], "граница seq")
edge_ok = (edge[0] == ("error", {"id": 0, "error": "seq_overflow", "code": "seq_overflow"})
           and edge[1][0] == "result" and edge[1][1]["snapshot"].get("seq") == M
           and edge[2][0] == "result" and edge[2][1]["snapshot"].get("seq") == M
           and validator.is_valid(edge[2][1]["snapshot"]))
print("граница seq:", "ok" if edge_ok else f"НЕТ {edge}")
sys.exit(1 if bad or not edge_ok else 0)

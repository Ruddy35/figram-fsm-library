"""Граничные случаи протокола движка, которых нет в сценариях (FORMAT.md §7, HOST.md §4).

Сценарии `figram trace` и фиксированные сценарии не посылают `restart`, частичный
набор условий, не-булевы условия, событие таймера без жетона и не проверяют
значение версии в `hello`. Мутационное тестирование (`Figram/Mutate.lean`) портит
только таблицы автомата и этих мест не касается. Ожидания выписаны вручную по
FORMAT.md и README («Решения спецификации»), а не сняты с движка.

    python3 protocol_edges.py ENGINE examples/features.bot.json
"""

import json
import sys

from protocol import ChildTimeout, ProtocolError, check_reply, default_timeout, run_child

M = 9007199254740991          # 2^53 − 1, верхняя граница seq и жетонов
ITEM = {"at": "item", "resume": {"shop": "item"}, "timers": {"remind": 3}, "seq": 5}
IDLE = {"start_timer": "idle", "ms": 60000}

CASES = [
    ("hello: версия протокола 1",
     {"op": "hello"}, {"protocol": 1, "states": 8}),
    ("restart у границы: seq продолжается до 2^53−1, таймеры отменены, места очищены",
     {"op": "restart", "snapshot": {"at": "item", "resume": {"shop": "item"}, "timers": {"remind": 7}, "seq": M - 1}},
     {"snapshot": {"at": "home", "timers": {"idle": M}, "seq": M},
      "actions": [{"cancel_timer": "remind"}, {**IDLE, "token": M}, {"send": "home"}]}),
    ("restart на границе: seq_overflow без снимка",
     {"op": "restart", "snapshot": {"at": "item", "seq": M}}, {"error": "seq_overflow"}),
    ("restart очищает запомненные места",
     {"op": "restart", "snapshot": {"at": "home", "resume": {"shop": "sub"}}},
     {"snapshot": {"at": "home", "timers": {"idle": 1}, "seq": 1},
      "actions": [{**IDLE, "token": 1}, {"send": "home"}]}),
    ("частичный набор условий: недостающее запрашивается",
     {"op": "step", "snapshot": {"at": "home"}, "event": "btn:go", "guards": {"other": True}},
     {"need": ["is_vip"]}),
    ("лишнее условие не мешает",
     {"op": "step", "snapshot": {"at": "home"}, "event": "btn:go", "guards": {"is_vip": True, "other": False}},
     {"snapshot": {"at": "vip", "seq": 1}, "actions": [{"cancel_timer": "idle"}, {"send": "vip"}]}),
    ("не-булево значение условия — ошибка",
     {"op": "step", "snapshot": {"at": "home"}, "event": "btn:go", "guards": {"is_vip": 1}}, "error"),
    ("событие таймера без жетона — ошибка",
     {"op": "step", "snapshot": ITEM, "event": "timer:remind"}, "error"),
    ("устаревший жетон игнорируется",
     {"op": "step", "snapshot": ITEM, "event": "timer:remind", "token": 2},
     {"snapshot": ITEM, "actions": []}),
    ("текущий жетон срабатывает",
     {"op": "step", "snapshot": ITEM, "event": "timer:remind", "token": 3},
     {"snapshot": {"at": "reminder", "resume": {"shop": "reminder"}, "seq": 6},
      "actions": [{"cancel_timer": "remind"}, {"send": "reminder"}]}),
    ("удалённое сообщение: восстановление с флагом recovered",
     {"op": "step", "snapshot": {"at": "gone", "timers": {"old": 4}, "seq": 9}, "event": "btn:x"},
     {"snapshot": {"at": "home", "timers": {"idle": 10}, "seq": 10},
      "actions": [{"cancel_timer": "old"}, {**IDLE, "token": 10}, {"send": "home"}], "recovered": True}),
]

engine, doc = sys.argv[1:3]
data = "".join(json.dumps({**req, "id": i}) + "\n" for i, (_, req, _) in enumerate(CASES))
try:
    out = run_child([engine, doc], data.encode(), default_timeout(), "граничные случаи протокола")
except ChildTimeout as e:
    raise SystemExit(str(e))
lines = out.stdout.decode().split("\n")
if lines and lines[-1] == "":
    lines.pop()
if out.returncode != 0 or len(lines) != len(CASES):
    raise SystemExit(f"ответов {len(lines)} из {len(CASES)}, код {out.returncode}")
bad = 0
for i, ((what, req, exp), line) in enumerate(zip(CASES, lines)):
    try:
        resp = json.loads(line)
        variant = check_reply(resp, i, req["op"])
    except (ValueError, ProtocolError) as e:
        bad += 1
        print(f"НЕТ {what}: ответ нарушает протокол: {e}: {line[:200]}")
        continue
    body = {k: v for k, v in resp.items() if k != "id"}
    ok = variant == "error" if exp == "error" else body == exp
    if not ok:
        bad += 1
        print(f"НЕТ {what}: {body} ≠ ожидалось {exp}")
print(f"граничных случаев протокола {len(CASES)}, ответов {len(lines)}, расхождений {bad}")
sys.exit(1 if bad else 0)

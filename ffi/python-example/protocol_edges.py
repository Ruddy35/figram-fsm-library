"""Граничные случаи протокола движка, которых нет в сценариях (FORMAT.md §7, HOST.md §4).

Сценарии `figram trace` и фиксированные сценарии не посылают `restart`, частичный
набор условий, не-булевы условия, событие таймера без жетона и не проверяют
значение версии в `hello`. Мутационное тестирование (`Figram/Mutate.lean`) портит
только таблицы автомата и этих мест не касается. Ожидания выписаны вручную по
FORMAT.md §7 и README («Решения спецификации»), а не сняты с движка.

Разделы:
* запросы JSON (поле `id` — номер случая): результат, `need`, ошибки с машинным
  кодом (`code`), строгие поля запроса, границы жетона и `seq`, `start` с `seq`;
* сырые строки байтов в одном процессе: эхо `id` при ошибке разбора, невалидный
  UTF-8, пустые строки без ответа, предел размера запроса (по умолчанию 1 МиБ),
  ответы строго в порядке запросов;
* `--max-request`: малый предел и 0 (без предела);
* командная строка: мутанты только при FIGRAM_ALLOW_MUTANTS=1, поле `mutant` в
  `hello`, неизвестная мутация и неверные аргументы — отказ.

    python3 protocol_edges.py ENGINE examples/features.bot.json
"""

import json
import os
import subprocess
import sys

from protocol import (ChildTimeout, ProtocolError, check_error, check_reply, default_timeout,
                      mutant_env, run_child)

M = 9007199254740991          # 2^53 − 1, верхняя граница seq и жетонов
MIB = 1048576                 # предел размера запроса по умолчанию
ITEM = {"at": "item", "resume": {"shop": "item"}, "timers": {"remind": 3}, "seq": 5}
IDLE = {"start_timer": "idle", "ms": 60000}
FIRED = {"snapshot": {"at": "reminder", "resume": {"shop": "reminder"}, "seq": 6},
         "actions": [{"cancel_timer": "remind"}, {"send": "reminder"}]}
IGNORED = {"snapshot": ITEM, "actions": []}
HELLO = {"protocol": 1, "states": 8}
H = {"at": "home"}


def err(code):
    """Ожидание «ошибка с этим кодом» (текст для человека не закреплён)."""
    return ("error", code)


def step(snapshot, event, **kw):
    return {"op": "step", "snapshot": snapshot, "event": event, **kw}


def started(seq):
    return {"snapshot": {"at": "home", "timers": {"idle": seq}, "seq": seq},
            "actions": [{**IDLE, "token": seq}, {"send": "home"}]}


CASES = [
    ("hello: версия протокола 1",
     {"op": "hello"}, HELLO),
    ("restart у границы: seq продолжается до 2^53−1, таймеры отменены, места очищены",
     {"op": "restart", "snapshot": {"at": "item", "resume": {"shop": "item"}, "timers": {"remind": 7}, "seq": M - 1}},
     {"snapshot": {"at": "home", "timers": {"idle": M}, "seq": M},
      "actions": [{"cancel_timer": "remind"}, {**IDLE, "token": M}, {"send": "home"}]}),
    ("restart на границе: seq_overflow без снимка, код seq_overflow",
     {"op": "restart", "snapshot": {"at": "item", "seq": M}}, {"error": "seq_overflow", "code": "seq_overflow"}),
    ("step на границе: seq_overflow без снимка, код seq_overflow",
     step({"at": "home", "seq": M}, "btn:self"), {"error": "seq_overflow", "code": "seq_overflow"}),
    ("restart очищает запомненные места",
     {"op": "restart", "snapshot": {"at": "home", "resume": {"shop": "sub"}}}, started(1)),
    ("частичный набор условий: недостающее запрашивается",
     step(H, "btn:go", guards={"other": True}), {"need": ["is_vip"]}),
    ("лишнее условие не мешает",
     step(H, "btn:go", guards={"is_vip": True, "other": False}),
     {"snapshot": {"at": "vip", "seq": 1}, "actions": [{"cancel_timer": "idle"}, {"send": "vip"}]}),
    ("не-булево значение условия — bad_request",
     step(H, "btn:go", guards={"is_vip": 1}), err("bad_request")),
    ("«guards» не объект — bad_request",
     step(H, "btn:go", guards=None), err("bad_request")),
    ("событие таймера без жетона — bad_request",
     step(ITEM, "timer:remind"), err("bad_request")),
    ("устаревший жетон игнорируется",
     step(ITEM, "timer:remind", token=2), IGNORED),
    ("текущий жетон срабатывает",
     step(ITEM, "timer:remind", token=3), FIRED),
    ("удалённое сообщение: восстановление с флагом recovered",
     step({"at": "gone", "timers": {"old": 4}, "seq": 9}, "btn:x"),
     {"snapshot": {"at": "home", "timers": {"idle": 10}, "seq": 10},
      "actions": [{"cancel_timer": "old"}, {**IDLE, "token": 10}, {"send": "home"}], "recovered": True}),
    # ---- жетон: целое 0…2^53−1 по значению -------------------------------
    ("жетон 3.0 — то же целое 3", step(ITEM, "timer:remind", token=3.0), FIRED),
    ("жетон 2^53−1 допустим (устаревший — игнорируется)", step(ITEM, "timer:remind", token=M), IGNORED),
    ("жетон 2^53 — bad_request", step(ITEM, "timer:remind", token=M + 1), err("bad_request")),
    ("жетон 2^64 — bad_request", step(ITEM, "timer:remind", token=2 ** 64), err("bad_request")),
    ("жетон −1 — bad_request", step(ITEM, "timer:remind", token=-1), err("bad_request")),
    ("жетон 1.5 — bad_request", step(ITEM, "timer:remind", token=1.5), err("bad_request")),
    ("жетон-строка — bad_request", step(ITEM, "timer:remind", token="3"), err("bad_request")),
    ("жетон true — bad_request", step(ITEM, "timer:remind", token=True), err("bad_request")),
    ("жетон null — bad_request", step(ITEM, "timer:remind", token=None), err("bad_request")),
    ("жетон у обычного события — bad_request", step(ITEM, "btn:other", token=3), err("bad_request")),
    # ---- событие ----------------------------------------------------------
    ("пустое событие — bad_request", step(H, ""), err("bad_request")),
    ("«timer:» без имени таймера — bad_request", step(ITEM, "timer:", token=3), err("bad_request")),
    ("событие-число — bad_request", step(H, 5), err("bad_request")),
    ("событие null — bad_request", step(H, None), err("bad_request")),
    ("нет события — bad_request", {"op": "step", "snapshot": H}, err("bad_request")),
    ("событие без стрелки игнорируется", step(H, "btn:nothing"), {"snapshot": H, "actions": []}),
    # ---- строгие поля запроса ---------------------------------------------
    ("hello с лишним полем — bad_request", {"op": "hello", "extra": 1}, err("bad_request")),
    ("start со снимком — bad_request", {"op": "start", "snapshot": H}, err("bad_request")),
    ("start с условиями — bad_request", {"op": "start", "guards": {}}, err("bad_request")),
    ("restart с событием — bad_request", {"op": "restart", "snapshot": H, "event": "btn:x"}, err("bad_request")),
    ("опечатка «gaurds» — bad_request, а не вечный need",
     step(H, "btn:go", gaurds={"is_vip": True}), err("bad_request")),
    ("поле «Token» (регистр) — bad_request", step(ITEM, "timer:remind", Token=3), err("bad_request")),
    ("неизвестная операция — bad_request", {"op": "HELLO"}, err("bad_request")),
    ("нет операции — bad_request", {}, err("bad_request")),
    ("операция-число — bad_request", {"op": 5}, err("bad_request")),
    # ---- снимок -----------------------------------------------------------
    ("недопустимый снимок шага — bad_snapshot", step({"at": ""}, "btn:self"), err("bad_snapshot")),
    ("снимок null — bad_snapshot", step(None, "btn:self"), err("bad_snapshot")),
    ("снимок более новой версии в restart — bad_snapshot",
     {"op": "restart", "snapshot": {"at": "home", "v": 2}}, err("bad_snapshot")),
    ("нет снимка у шага — bad_request", {"op": "step", "event": "btn:self"}, err("bad_request")),
    ("нет снимка у restart — bad_request", {"op": "restart"}, err("bad_request")),
    # ---- start с seq ------------------------------------------------------
    ("start без seq: первый переход получает seq 1", {"op": "start"}, started(1)),
    ("start с seq 0 — как без seq", {"op": "start", "seq": 0}, started(1)),
    ("start с seq 41: первый переход получает seq 42", {"op": "start", "seq": 41}, started(42)),
    ("start с seq 2^53−2: seq 2^53−1", {"op": "start", "seq": M - 1}, started(M)),
    ("start с seq 2^53−1 — seq_overflow", {"op": "start", "seq": M}, {"error": "seq_overflow", "code": "seq_overflow"}),
    ("start с seq 2^53 — bad_request", {"op": "start", "seq": M + 1}, err("bad_request")),
    ("start с seq −1 — bad_request", {"op": "start", "seq": -1}, err("bad_request")),
    ("start с seq 1.5 — bad_request", {"op": "start", "seq": 1.5}, err("bad_request")),
    ("start с seq-строкой — bad_request", {"op": "start", "seq": "1"}, err("bad_request")),
    ("start с seq 7.0 — то же целое 7", {"op": "start", "seq": 7.0}, started(8)),
]

ITEM_TXT = json.dumps(ITEM).encode()
ZERO_TXT = b'{"at":"item","resume":{"shop":"item"},"timers":{"remind":0}}'
FIRED_ZERO = {"snapshot": {"at": "reminder", "resume": {"shop": "reminder"}, "seq": 1},
              "actions": [{"cancel_timer": "remind"}, {"send": "reminder"}]}


def pad_to(line: bytes, n: int) -> bytes:
    """Дополнить строку запроса пробелами (пробел JSON) до длины n байт."""
    assert len(line) <= n
    return line + b" " * (n - len(line))


# Сырые строки: (что, байты строки без \n, ожидание). Ожидание: dict — ответ целиком
# (с id); ("error", id, code) — ошибка с этим id и кодом; None — ответа нет.
RAW = [
    ("ошибка разбора (текст после JSON): эхо id", b'{"id":100,"op":"hello"} x', ("error", 100, "parse_error")),
    ("повтор ключа в снимке: эхо id",
     b'{"id":101,"op":"step","snapshot":{"at":"home","at":"vip"},"event":"btn:self"}', ("error", 101, "parse_error")),
    ("вложенность больше 256: эхо id",
     b'{"id":102,"op":"step","guards":' + b"[" * 300 + b"]" * 300 + b"}", ("error", 102, "parse_error")),
    ("показатель больше 1000: эхо id", b'{"id":103,"op":"hello","x":1e5000}', ("error", 103, "parse_error")),
    ("не JSON: id null", b"hello", ("error", None, "parse_error")),
    ("эхо строкового id после пробелов", b'  {  "id" : "abc" , "op":', ("error", "abc", "parse_error")),
    ("эхо id true", b'{"id":true,"op":}', ("error", True, "parse_error")),
    ("эхо id null", b'{"id":null,"op":}', ("error", None, "parse_error")),
    ("эхо id по значению: -0 → 0", b'{"id":-0,"op":}', ("error", 0, "parse_error")),
    ("эхо id по значению: 1.5e3", b'{"id":1.5e3,"op":}', ("error", 1500, "parse_error")),
    ("id-объект при ошибке разбора: null", b'{"id":{"a":1},"op":}', ("error", None, "parse_error")),
    ("после id нет «,» или «}»: null", b'{"id":12x,"op":"hello"}', ("error", None, "parse_error")),
    ("id не первым полем: null", b'{"op":"hello","id":5', ("error", None, "parse_error")),
    ("невалидный UTF-8 в событии: parse_error, эхо id",
     b'{"id":104,"op":"step","snapshot":{"at":"home"},"event":"\xff"}', ("error", 104, "parse_error")),
    ("невалидный UTF-8 в самом id: null", b'{"id":"\xff","op":"hello"}', ("error", None, "parse_error")),
    ("CESU-суррогат: parse_error", b'{"id":105,"op":"hello","x":"\xed\xa0\x80"}', ("error", 105, "parse_error")),
    ("overlong-запись «/»: parse_error", b'{"id":106,"op":"hello","x":"\xc0\xaf"}', ("error", 106, "parse_error")),
    ("код больше U+10FFFF: parse_error", b'{"id":107,"op":"hello","x":"\xf4\x90\x80\x80"}', ("error", 107, "parse_error")),
    ("оборванная последовательность UTF-8: parse_error",
     b'{"id":108,"op":"hello","x":"\xe2\x82"}', ("error", 108, "parse_error")),
    ("пустая строка — без ответа", b"", None),
    ("строка из пробелов, табуляций и CR — без ответа", b"  \t\r", None),
    ("BOM перед запросом: parse_error, id null", b'\xef\xbb\xbf{"id":109,"op":"hello"}', ("error", None, "parse_error")),
    ("CRLF: CR — пробел JSON", b'{"id":110,"op":"hello"}\r', {"id": 110, **HELLO}),
    ("запрос не объект: bad_request, id null", b"[1]", ("error", None, "bad_request")),
    ("жетон -0 = 0",
     b'{"id":111,"op":"step","snapshot":' + ZERO_TXT + b',"event":"timer:remind","token":-0}', {"id": 111, **FIRED_ZERO}),
    ("жетон 3e0 = 3",
     b'{"id":112,"op":"step","snapshot":' + ITEM_TXT + b',"event":"timer:remind","token":3e0}', {"id": 112, **FIRED}),
    ("жетон 30e-1 = 3",
     b'{"id":113,"op":"step","snapshot":' + ITEM_TXT + b',"event":"timer:remind","token":30e-1}', {"id": 113, **FIRED}),
    ("start с seq -0 — как без seq", b'{"id":114,"op":"start","seq":-0}', {"id": 114, **started(1)}),
    ("ровно 1 МиБ — обрабатывается", pad_to(b'{"id":115,"op":"hello"}', MIB), {"id": 115, **HELLO}),
    ("1 МиБ + 1 байт — too_large, эхо id", pad_to(b'{"id":116,"op":"hello"}', MIB + 1), ("error", 116, "too_large")),
    ("слишком большой запрос без id в начале — too_large, null",
     b'{"pad":"' + b"x" * MIB + b'"}', ("error", None, "too_large")),
    ("после too_large процесс читает следующую строку", b'{"id":117,"op":"hello"}', {"id": 117, **HELLO}),
    ("строка из пробелов длиннее предела — без ответа", b" " * (2 * MIB), None),
    ("последняя строка без \\n обрабатывается", b'{"id":118,"op":"hello"}', {"id": 118, **HELLO}),
]

LIMITED = [  # figram-engine --max-request 64
    ("--max-request 64: ровно 64 байта", pad_to(b'{"id":200,"op":"hello"}', 64), {"id": 200, **HELLO}),
    ("--max-request 64: 65 байт — too_large", pad_to(b'{"id":201,"op":"hello"}', 65), ("error", 201, "too_large")),
    ("--max-request 64: id длиннее предела всё равно отражается",
     pad_to(b'{"id":"' + b"y" * 100 + b'","op":"hello"}', 200), ("error", "y" * 100, "too_large")),
    ("--max-request 64: следующая строка обрабатывается", b'{"id":202,"op":"hello"}', {"id": 202, **HELLO}),
]

UNLIMITED = [  # figram-engine --max-request 0
    ("--max-request 0: строка 3 МиБ обрабатывается", pad_to(b'{"id":300,"op":"hello"}', 3 * MIB), {"id": 300, **HELLO}),
]

engine, doc = sys.argv[1:3]
bad = 0
answered = 0
total = 0


def fail(what, msg):
    global bad
    bad += 1
    print(f"НЕТ {what}: {msg}")


def lines_of(out):
    lines = out.stdout.decode("utf-8").split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def same_id(got, exp) -> bool:
    """id ответа равен ожидаемому по значению и по типу JSON (true ≠ 1)."""
    if exp is None or type(exp) is bool:
        return got is exp
    if type(exp) is str:
        return type(got) is str and got == exp
    return type(got) in (int, float) and got == exp


# ---- запросы JSON ------------------------------------------------------------
data = b"".join(json.dumps({**req, "id": i}).encode() + b"\n" for i, (_, req, _) in enumerate(CASES))
try:
    out = run_child([engine, doc], data, default_timeout(), "граничные случаи протокола")
except ChildTimeout as e:
    raise SystemExit(str(e))
lines = lines_of(out)
total += len(CASES)
if out.returncode != 0 or len(lines) != len(CASES):
    fail("запросы JSON", f"ответов {len(lines)} из {len(CASES)}, код {out.returncode}")
for i, ((what, req, exp), line) in enumerate(zip(CASES, lines)):
    answered += 1
    try:
        resp = json.loads(line)
        variant = check_reply(resp, i, req["op"] if type(req.get("op")) is str else "")
    except (ValueError, ProtocolError) as e:
        fail(what, f"ответ нарушает протокол: {e}: {line[:200]}")
        continue
    body = {k: v for k, v in resp.items() if k != "id"}
    ok = variant == "error" and resp["code"] == exp[1] if isinstance(exp, tuple) else body == exp
    if not ok:
        fail(what, f"{body} ≠ ожидалось {exp}")


def run_raw(section, args, cases, final_newline=True):
    """Сырые строки в одном процессе; ответы — строго по порядку строк."""
    global answered, total
    total += len(cases)
    data = b"\n".join(line for _, line, _ in cases) + (b"\n" if final_newline else b"")
    try:
        out = run_child([engine] + args + [doc], data, default_timeout(), section)
    except ChildTimeout as e:
        raise SystemExit(str(e))
    lines = lines_of(out)
    expected = [c for c in cases if c[2] is not None]
    if out.returncode != 0 or len(lines) != len(expected):
        fail(section, f"ответов {len(lines)}, ожидалось {len(expected)}, код {out.returncode}: "
                      f"{out.stderr.decode(errors='replace')[:200]}")
        return
    answered += len(cases) - len(expected)      # отсутствие ответа на пустые строки проверено
    for (what, _, exp), line in zip(expected, lines):
        answered += 1
        try:
            resp = json.loads(line)
            if isinstance(exp, tuple):
                code = check_error(resp)
                ok = same_id(resp["id"], exp[1]) and code == exp[2]
            else:
                check_reply(resp, exp["id"], "hello" if "protocol" in exp else "step")
                ok = resp == exp
        except (ValueError, ProtocolError) as e:
            fail(what, f"ответ нарушает протокол: {e}: {line[:200]}")
            continue
        if not ok:
            fail(what, f"{resp} ≠ ожидалось {exp}")


run_raw("сырые строки", [], RAW, final_newline=False)
run_raw("--max-request 64", ["--max-request", "64"], LIMITED)
run_raw("--max-request 0", ["--max-request", "0"], UNLIMITED)

# ---- командная строка: мутанты только в тестовом режиме, неверные аргументы ----
no_mut = {k: v for k, v in os.environ.items() if k != "FIGRAM_ALLOW_MUTANTS"}


def cli(args, data=b"", env=None):
    return subprocess.run([engine] + args, input=data, capture_output=True, timeout=default_timeout(), env=env)


def check_cli(what, cond, r):
    global answered, total
    total += 1
    answered += 1
    if not cond:
        fail(what, f"код {r.returncode}, stdout {r.stdout[:120]!r}, stderr {r.stderr[:200]!r}")


hello = b'{"id":1,"op":"hello"}\n'
r = cli(["--mutants", doc], env=no_mut)
check_cli("--mutants без FIGRAM_ALLOW_MUTANTS — отказ",
          r.returncode != 0 and r.stdout == b"" and b"FIGRAM_ALLOW_MUTANTS" in r.stderr, r)
r = cli(["--mutants", doc], env=dict(no_mut, FIGRAM_ALLOW_MUTANTS="0"))
check_cli("--mutants при FIGRAM_ALLOW_MUTANTS=0 — отказ", r.returncode != 0 and r.stdout == b"", r)
r = cli(["--mutants", doc], env=mutant_env())
mutants = r.stdout.decode().split()
check_cli("--mutants при FIGRAM_ALLOW_MUTANTS=1 — список", r.returncode == 0 and len(mutants) > 0, r)
spec = mutants[0] if mutants else "drop-call:charge"
r = cli(["--mutate", spec, doc], hello, env=no_mut)
check_cli("--mutate без FIGRAM_ALLOW_MUTANTS — отказ, ответа нет", r.returncode != 0 and r.stdout == b"", r)
r = cli(["--mutate", spec, doc], hello, env=mutant_env())
try:
    mresp = json.loads(r.stdout)
except ValueError:
    mresp = None
check_cli("--mutate: hello сообщает мутацию, предупреждение в stderr",
          r.returncode == 0 and mresp == {"id": 1, **HELLO, "mutant": spec} and spec.encode() in r.stderr, r)
r = cli(["--mutate", "redirect:999", doc], hello, env=mutant_env())
check_cli("--mutate redirect:999 (номера нет в автомате) — отказ", r.returncode == 2 and r.stdout == b"", r)
r = cli(["--mutate", "drop-call:nope", doc], hello, env=mutant_env())
check_cli("--mutate drop-call:nope (действия нет в автомате) — отказ", r.returncode == 2 and r.stdout == b"", r)
r = cli(["--mutate", "bogus", doc], hello, env=mutant_env())
check_cli("--mutate bogus — отказ", r.returncode != 0 and r.stdout == b"", r)
for bad_args in (["--max-request", "abc", doc], ["--max-request", "-5", doc], ["--max-request", doc],
                 ["--bogus", doc], []):
    r = cli(bad_args, hello)
    check_cli(f"аргументы {bad_args} — отказ с кодом 2", r.returncode == 2 and r.stdout == b"", r)
r = cli(["--max-request", "100", doc], hello)
try:
    lresp = json.loads(r.stdout)
except ValueError:
    lresp = None
check_cli("--max-request 100 перед документом — работает", r.returncode == 0 and lresp == {"id": 1, **HELLO}, r)

print(f"граничных случаев протокола {total}, ответов {answered}, расхождений {bad}")
sys.exit(1 if bad else 0)

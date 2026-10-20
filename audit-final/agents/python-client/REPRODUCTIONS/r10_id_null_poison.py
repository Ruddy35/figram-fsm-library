"""PC-10 (гипотеза FS-4 агента formats): строка запроса, которую движок не
может разобрать на текстовом уровне, получает ответ {"id":null,"error":...};
клиент считает ответ без целого id нарушением протокола, убивает процесс и
будит ошибкой ВСЕ ожидающие запросы других пользователей этого процесса.

Что клиент реально отправляет (печатается строка «на проводе»):
  dup-int   снимок с ключами 1 и "1" в resume → json.dumps даёт дубль "1"
  dup-bool  снимок с ключами True и "true" в timers → дубль "true"
  deep      снимок с вложенностью 300 → «вложенность больше 256»
  surrogate одиночный суррогат → до движка не доходит (см. PC-01)
  control   неизвестное поле "x" (обычная семантическая ошибка с id) — контроль

Нагрузка: 7 обычных пользователей шагают в цикле; плохой запрос повторяется
хостом REPEAT раз (HOST.md §5). Настоящий движок.

    python3 r10_id_null_poison.py [CASE ...]      # exit 1 = гипотеза подтверждена
"""
import json
import sys
import threading
import time

from _common import ENGINE, PIZZA, label
from figram_client import Engine, EngineError

label()
deep = {}
cur = deep
for _ in range(300):
    cur["a"] = {}
    cur = cur["a"]
CASES = {
    "dup-int": {"at": "menu", "resume": {1: "menu", "1": "menu"}},
    "dup-bool": {"at": "menu", "timers": {True: 1, "true": 2}},
    "deep": {"at": "menu", "x": deep},
    "surrogate": {"at": "menu\ud800"},
    "control": {"at": "menu", "x": 1},
}
REPEAT = 20
names = sys.argv[1:] or list(CASES)
confirmed = False
for name in names:
    bad = CASES[name]
    try:
        wire = json.dumps({"op": "step", "snapshot": bad, "event": "btn:x"}, ensure_ascii=False,
                          separators=(",", ":"), allow_nan=False)
        wire = wire.encode()
        shown = wire[:90] + (b"..." if len(wire) > 90 else b"")
    except Exception as e:  # noqa: BLE001
        shown = f"не сериализуется: {type(e).__name__}"
    eng = Engine(ENGINE, PIZZA, processes=1, timeout=5)
    pids = {eng._procs[0]._p.pid}
    stop = threading.Event()
    stats = {"ok": 0, "err": 0}
    lk = threading.Lock()

    def normal(u):
        snap = eng.start(u)["snapshot"]
        while not stop.is_set():
            try:
                eng.step(u, snap, "btn:order")
                with lk:
                    stats["ok"] += 1
            except EngineError:
                with lk:
                    stats["err"] += 1

    ts = [threading.Thread(target=normal, args=(f"u{i}",)) for i in range(7)]
    for t in ts:
        t.start()
    time.sleep(0.3)
    bad_exc = {}
    for _ in range(REPEAT):
        try:
            eng.step("evil", bad, "btn:x")
            bad_exc["ответ"] = bad_exc.get("ответ", 0) + 1
        except Exception as e:  # noqa: BLE001
            msg = f"{type(e).__name__}: {str(e)[:60]}"
            bad_exc[msg] = bad_exc.get(msg, 0) + 1
        pids.add(eng._procs[0]._p.pid)
        time.sleep(0.05)
    stop.set()
    for t in ts:
        t.join()
    eng.close()
    print(f"[{name}] на проводе: {shown}")
    print(f"[{name}] плохой запрос ×{REPEAT}: {bad_exc}")
    print(f"[{name}] обычные пользователи: успешно {stats['ok']}, ошибок {stats['err']}; процессов движка {len(pids)}\n", flush=True)
    if name != "control" and stats["err"] > 0:
        confirmed = True
print("FS-4 ПОДТВЕРЖДЕНА: ответ id:null убивает процесс и соседей" if confirmed else "FS-4 не подтверждена")
raise SystemExit(1 if confirmed else 0)

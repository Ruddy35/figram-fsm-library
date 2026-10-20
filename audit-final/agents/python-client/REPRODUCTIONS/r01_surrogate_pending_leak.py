"""PC-01: строка с одиночным суррогатом (например, обрезанный эмодзи из текста
Telegram, разрезанного по UTF-16-смещениям) в запросе.

Ожидание по контракту V4-03: «несериализуемый аргумент — EngineError,
таблица ожидающих и процесс не затрагиваются».
Факт (baseline): UnicodeEncodeError (не EngineError), а запись в _pending
остаётся навсегда — на каждый такой запрос.

    python3 r01_surrogate_pending_leak.py      # exit 1 = дефект воспроизведён
"""
from _common import ENGINE, PIZZA, label
from figram_client import Engine, EngineError

label()
eng = Engine(ENGINE, PIZZA, timeout=2)
p = eng._procs[0]
kinds = {}
N = 1000
for i in range(N):
    try:
        eng.step("u", {"at": "menu"}, "btn:\ud83d" + str(i))
        kinds["ответ"] = kinds.get("ответ", 0) + 1
    except Exception as e:  # noqa: BLE001
        kinds[type(e).__name__] = kinds.get(type(e).__name__, 0) + 1
print("исключения:", kinds)
print("ожидающих в таблице после", N, "запросов:", len(p._pending), "процесс жив:", p.alive)
alive_ok = "snapshot" in eng.start("u")
print("процесс обслуживает обычные запросы:", alive_ok)
eng.close()
bad = kinds.get("EngineError", 0) != N or len(p._pending) != 0
print("ДЕФЕКТ ВОСПРОИЗВЕДЁН" if bad else "дефекта нет")
raise SystemExit(1 if bad else 0)

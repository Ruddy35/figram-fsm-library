"""PC-03: бинарник движка исчез/не исполняется к моменту замены процесса
(выкладка новой версии, неверный путь в конфиге, EMFILE при fork).

Ожидание: вызов завершается EngineError (хост по HOST.md §5 ловит её и
повторяет). Факт (baseline): из step()/start() вылетает FileNotFoundError /
PermissionError — исключение не из контракта клиента.

    python3 r03_missing_binary.py      # exit 1 = дефект воспроизведён
"""
import os
import shutil
import tempfile

from _common import ENGINE, PIZZA, label
from figram_client import Engine, EngineError

label()
with tempfile.TemporaryDirectory() as d:
    copy = os.path.join(d, "figram-engine")
    shutil.copy(ENGINE, copy)
    eng = Engine(copy, PIZZA, timeout=2)
    eng.start("u")
    os.remove(copy)                       # выкладка удалила старый бинарник
    eng._procs[0]._fail("внедрённый сбой")  # процесс упал
    seen = {}
    for _ in range(3):
        try:
            eng.start("u")
            seen["ответ"] = seen.get("ответ", 0) + 1
        except Exception as e:  # noqa: BLE001
            seen[type(e).__name__] = seen.get(type(e).__name__, 0) + 1
    print("исключения при замене процесса без бинарника:", seen)
    eng.close()
bad = set(seen) != {"EngineError"}
print("ДЕФЕКТ ВОСПРОИЗВЕДЁН" if bad else "дефекта нет")
raise SystemExit(1 if bad else 0)

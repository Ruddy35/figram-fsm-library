"""PC-02: вызов после Engine.close().

Ожидание: EngineError «клиент закрыт», новых процессов нет.
Факт (baseline): закрытый процесс считается «неисправным», _proc() молча
запускает НОВЫЙ процесс движка, который больше никто не закроет (утечка
процесса, двух потоков и трёх дескрипторов на каждый ключ/слот).

    python3 r02_use_after_close.py      # exit 1 = дефект воспроизведён
"""
import threading
import time

from _common import ENGINE, PIZZA, children, label, nfds
from figram_client import Engine, EngineError

label()
eng = Engine(ENGINE, PIZZA, processes=2, timeout=2)
eng.start("a")
eng.close()
time.sleep(0.3)
before = (len(children()), threading.active_count(), nfds())
outcomes = []
for key in ("a", "b", "c", "d"):
    for _ in range(2):
        try:
            r = eng.start(key)
            outcomes.append("ответ " + r["snapshot"]["at"])
        except EngineError as e:
            outcomes.append("EngineError")
time.sleep(0.3)
after = (len(children()), threading.active_count(), nfds())
print("после close: потомков/потоков/fd до вызовов", before, "после", after)
print("исходы вызовов после close:", outcomes)
leaked = after[0] - before[0]
print("живых процессов движка, запущенных после close:", leaked)
bad = leaked > 0 or any(o.startswith("ответ") for o in outcomes)
print("ДЕФЕКТ ВОСПРОИЗВЕДЁН" if bad else "дефекта нет")
raise SystemExit(1 if bad else 0)

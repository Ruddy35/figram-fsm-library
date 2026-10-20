"""PC-06: один медленный запрос («ядовитый»: на нём движок не укладывается в
timeout) + повтор по HOST.md §5 → процесс убивается снова и снова, и все
пользователи, попавшие (по хешу) в тот же процесс, получают ошибки.

Медленный запрос — шаг со снимком, где resume содержит RESUME записей
(настоящий движок разбирает такой снимок за секунды: время растёт
квадратично, см. LOGS/*big_snap_timing*). 7 обычных пользователей делают
шаги с повтором; печатается доля успешных шагов без яда и с ядом.

    python3 r06_poison_request.py [RESUME] [SECONDS]
"""
import sys
import threading
import time

from _common import ENGINE, PIZZA, label
from figram_client import Engine, EngineError

resume = int(sys.argv[1]) if len(sys.argv) > 1 else 30000
seconds = float(sys.argv[2]) if len(sys.argv) > 2 else 8
label()
POISON = {"at": "menu", "resume": {f"s{i}": "m" for i in range(resume)}}


def run(with_poison: bool):
    eng = Engine(ENGINE, PIZZA, processes=1, timeout=2.0)
    stop = time.monotonic() + seconds
    stats = {"ok": 0, "err": 0, "poison_err": 0, "kills": set()}
    lk = threading.Lock()

    def normal(u):
        snap = eng.start(u)["snapshot"]
        while time.monotonic() < stop:
            stats["kills"].add(eng._procs[0]._p.pid)
            try:
                eng.step(u, snap, "btn:none", lambda ns: {n: True for n in ns})
                with lk:
                    stats["ok"] += 1
            except EngineError:
                with lk:
                    stats["err"] += 1
            time.sleep(0.01)

    def poison():
        while time.monotonic() < stop:   # хост повторяет запрос, завершённый ошибкой
            try:
                eng.step("evil", POISON, "btn:x")
            except EngineError:
                with lk:
                    stats["poison_err"] += 1

    ts = [threading.Thread(target=normal, args=(f"u{i}",)) for i in range(7)]
    if with_poison:
        ts.append(threading.Thread(target=poison))
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    eng.close()
    total = stats["ok"] + stats["err"]
    print(f"{'с ядом ' if with_poison else 'без яда'}: шагов обычных пользователей {total}, "
          f"успешных {stats['ok']} ({100*stats['ok']/max(total,1):.1f}%), ошибок {stats['err']}, "
          f"ошибок ядовитого запроса {stats['poison_err']}, процессов движка {len(stats['kills'])}", flush=True)
    return stats


a = run(False)
b = run(True)
bad = b["err"] > 0 and a["err"] == 0
print("ДЕФЕКТ ВОСПРОИЗВЕДЁН: один запрос выводит из строя соседей по процессу" if bad else "эффекта нет")
raise SystemExit(1 if bad else 0)

"""Ресурсы клиента после многих циклов «сбой → замена процесса».

Сценарии (CYCLES по умолчанию 500, THREADS вызывающих потоков):
  kill      настоящий движок, процесс убивается SIGKILL снаружи
  inject    настоящий движок, внедрённый _fail() (как в client_faults.py)
  segv      подставной движок (test double): падает на каждом запросе
  badjson   подставной движок: невалидный JSON на каждом запросе
  hang      подставной движок: не отвечает (таймаут 0.05 с)
  baddoc    настоящий движок с невалидным документом: выход 1 при старте

Печатает fd / потоки Python / потоки ОС / потомков / зомби / RSS каждые 100
циклов. exit 1, если после завершения что-то из этого выросло сверх допуска.

    python3 r04_crash_cycles.py SCENARIO [CYCLES] [THREADS]
"""
import gc
import os
import signal
import sys
import tempfile
import threading
import time

from _common import ENGINE, PIZZA, children, fake, label, nfds, rss_mb
from figram_client import Engine, EngineError

scenario = sys.argv[1]
cycles = int(sys.argv[2]) if len(sys.argv) > 2 else 500
nthreads = int(sys.argv[3]) if len(sys.argv) > 3 else 1
label()
tmp = tempfile.mkdtemp()
binary, doc, timeout = ENGINE, PIZZA, 2.0
if scenario == "segv":
    binary = fake(tmp, "segv.sh", 'read l; kill -SEGV $$')
elif scenario == "badjson":
    binary = fake(tmp, "badjson.sh", 'read l; echo "{oops"; sleep 30')
elif scenario == "hang":
    binary, timeout = fake(tmp, "hang.sh", 'while read l; do :; done'), 0.05
elif scenario == "baddoc":
    doc = os.path.join(tmp, "bad.bot.json")
    with open(doc, "w") as f:
        f.write('{"this is": "not a figram document"}')


def snap():
    gc.collect()
    ch = children()
    return {"fd": nfds(), "py_threads": threading.active_count(),
            "os_threads": len(os.listdir("/proc/self/task")),
            "children": len(ch), "zombies": sum(s == "Z" for _, s in ch), "rss_mb": round(rss_mb(), 1)}


eng = Engine(binary, doc, processes=1, timeout=timeout)
if scenario in ("kill", "inject"):
    eng.start("u")
base = snap()
print("старт:", base, flush=True)
spawned = set()
errors = ok = 0
lock = threading.Lock()
t0 = time.monotonic()
for c in range(1, cycles + 1):
    p = eng._procs[0]
    spawned.add(p._p.pid)
    if scenario == "kill":
        try:
            os.kill(p._p.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        while p.alive:
            time.sleep(0.001)
    elif scenario == "inject":
        p._fail("внедрённый сбой")

    def worker():
        global errors, ok
        try:
            eng.start("u")
            with lock:
                ok += 1
        except EngineError:
            with lock:
                errors += 1

    ts = [threading.Thread(target=worker) for _ in range(nthreads)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(30)
    if c % 100 == 0:
        print(f"цикл {c}: {snap()}  ответов {ok} ошибок {errors}  процессов запущено {len(spawned)}", flush=True)
took = time.monotonic() - t0
eng.close()
time.sleep(0.5)
end = snap()
print(f"после close: {end}  ({took:.1f} с на {cycles} циклов, {len(spawned)/took:.0f} запусков процесса/с)")
grew = {k: end[k] - base[k] for k in ("fd", "py_threads", "os_threads", "children", "zombies")
        if end[k] - base[k] > 2}
print("РОСТ РЕСУРСОВ:" if grew else "роста ресурсов нет", grew if grew else "")
raise SystemExit(1 if grew else 0)

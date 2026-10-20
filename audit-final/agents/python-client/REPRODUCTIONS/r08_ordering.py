"""PC-08: утверждение figram_client.py «пользователь всегда попадает в один и
тот же процесс, поэтому его события обрабатываются строго по порядку
отправки».

(a) Без блокировки на пользователя (её требует HOST.md §1.1, но docstring
    клиента внушает, что порядок обеспечивает пул) два события одного
    пользователя читают один снимок: настоящий движок, одно событие теряется.
(b) Процесс падает, когда в нём два запроса одного пользователя (e1 отправлен
    раньше e2). Оба получают EngineError, хост повторяет — порядок повторов
    решает планировщик потоков. Подставной движок (test double) — прокси к
    настоящему движку, который один раз «падает» на e1 через 0.2 с.

    python3 r08_ordering.py [TRIALS]
"""
import os
import sys
import tempfile
import threading
import time

from _common import ENGINE, PIZZA, fake, label
from figram_client import Engine, EngineError

trials = int(sys.argv[1]) if len(sys.argv) > 1 else 40
label()

# (a) потерянное обновление
eng = Engine(ENGINE, PIZZA, processes=4, timeout=5)
db = {"u": eng.start("u")["snapshot"]}
barrier = threading.Barrier(2)


def host_without_lock(event):
    snap = db["u"]                 # чтение снимка
    barrier.wait()
    db["u"] = eng.step("u", snap, event, lambda ns: {n: True for n in ns})["snapshot"]


ts = [threading.Thread(target=host_without_lock, args=(e,)) for e in ("btn:order", "cmd:help")]
for t in ts:
    t.start()
for t in ts:
    t.join()
seq_ref = eng.step("u", eng.step("u", eng.start("u")["snapshot"], "btn:order")["snapshot"], "cmd:help")["snapshot"]
print(f"(a) два события без блокировки: итоговый снимок {db['u']} — последовательно было бы {seq_ref}")
lost = db["u"].get("seq") != seq_ref.get("seq")
eng.close()

# (b) порядок повторов после замены процесса
tmp = tempfile.mkdtemp()
marker = os.path.join(tmp, "crashed")
proxy = fake(tmp, "proxy.py", f'''
import os, subprocess, sys, time
real = subprocess.Popen([{ENGINE!r}, sys.argv[1]], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
for line in sys.stdin.buffer:
    if b'"btn:order"' in line and not os.path.exists({marker!r}):
        open({marker!r}, "w").close()
        time.sleep(0.2)
        os._exit(1)            # падение, пока e2 стоит в очереди за e1
    real.stdin.write(line); real.stdin.flush()
    sys.stdout.buffer.write(real.stdout.readline()); sys.stdout.buffer.flush()
''', python=True)
reordered = 0
for trial in range(trials):
    if os.path.exists(marker):
        os.remove(marker)
    eng = Engine(proxy, PIZZA, processes=1, timeout=5)
    s0 = eng.start("u")["snapshot"]
    commits = []
    lk = threading.Lock()

    def host(event, delay):
        time.sleep(delay)
        while True:
            try:
                r = eng.step("u", s0, event, lambda ns: {n: True for n in ns})
                with lk:
                    commits.append(event)
                return
            except EngineError:
                continue   # HOST.md §5: повторить

    ts = [threading.Thread(target=host, args=("btn:order", 0.0)),
          threading.Thread(target=host, args=("cmd:help", 0.05))]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    eng.close()
    if commits == ["cmd:help", "btn:order"]:
        reordered += 1
print(f"(b) e1 отправлен раньше e2, процесс упал с обоими в очереди: e2 зафиксирован раньше e1 "
      f"в {reordered} из {trials} прогонов")
print("утверждение о порядке НЕ выполняется без блокировки на пользователя" if lost or reordered else "нарушений не видно")
raise SystemExit(1 if lost or reordered else 0)

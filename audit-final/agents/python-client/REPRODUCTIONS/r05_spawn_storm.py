"""PC-05: процесс не может стартовать (невалидный документ: движок выходит с
кодом 1 сразу). Хост по HOST.md §5 повторяет неудавшиеся запросы.

Вопросы: есть ли пауза между перезапусками (backoff)? что видит хост в
тексте ошибки? Печатает число запусков процесса за SECONDS секунд при
THREADS потоках, повторяющих запрос в цикле, и процессорное время потомков.

    python3 r05_spawn_storm.py [SECONDS] [THREADS]
"""
import collections
import os
import sys
import tempfile
import threading
import time

from _common import ENGINE, children, label
from figram_client import Engine, EngineError

seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 5
nthreads = int(sys.argv[2]) if len(sys.argv) > 2 else 8
label()
tmp = tempfile.mkdtemp()
doc = os.path.join(tmp, "bad.bot.json")
with open(doc, "w") as f:
    f.write('{"this is": "not a figram document"}')

eng = Engine(ENGINE, doc, processes=1, timeout=2)
spawned = set()
msgs = collections.Counter()
stop = time.monotonic() + seconds
calls = 0
lk = threading.Lock()


def host_retry_loop():
    global calls
    while time.monotonic() < stop:
        spawned.add(eng._procs[0]._p.pid)
        try:
            eng.start("u")
        except EngineError as e:
            with lk:
                calls += 1
                msgs[str(e)] += 1


t0 = os.times()
ts = [threading.Thread(target=host_retry_loop) for _ in range(nthreads)]
for t in ts:
    t.start()
for t in ts:
    t.join()
eng.close()
time.sleep(0.3)
t1 = os.times()
print(f"{seconds} с, {nthreads} потоков: вызовов {calls}, запущено процессов движка {len(spawned)} "
      f"({len(spawned)/seconds:.0f}/с), процессорное время потомков {t1.children_user + t1.children_system - t0.children_user - t0.children_system:.2f} с")
print("тексты ошибок, которые видит хост:", dict(msgs.most_common(3)))
print("осталось потомков:", children())

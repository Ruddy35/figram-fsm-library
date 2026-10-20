"""Функция условий (guards) и close(): настоящий движок, features.bot.json
(btn:go из home требует условие is_vip).

  a) исключение внутри guards: какое исключение у вызывающего, течёт ли _pending
  b) повторный вход: guards сам вызывает клиент (тот же и другой ключ)
  c) guards дольше timeout: укладывается ли step (у второго вызова свой срок)
  d) close() во время активных вызовов из 8 потоков: зависания, ресурсы
  e) close() во время работы guards

    python3 r11_guards_close.py
"""
import threading
import time

from _common import ENGINE, FEATURES, children, label
from figram_client import Engine, EngineError

label()
eng = Engine(ENGINE, FEATURES, processes=2, timeout=1.0)
snap = eng.start("x")["snapshot"]


class Boom(Exception):
    pass


def raising(ns):
    raise Boom("ошибка в коде разработчика")


try:
    eng.step("x", snap, "btn:go", guards=raising)
    a = "ответ"
except Exception as e:  # noqa: BLE001
    a = type(e).__name__
pend = sum(len(p._pending) for p in eng._procs)
print(f"a) исключение в guards → у вызывающего {a}; ожидающих {pend}; процессы живы {[p.alive for p in eng._procs]}")


def reentrant(ns):
    eng.start("x")
    eng.step("other", snap, "btn:go", guards=lambda n2: {n: False for n in n2})
    return {n: True for n in ns}


t0 = time.monotonic()
r = eng.step("x", snap, "btn:go", guards=reentrant)
print(f"b) повторный вход в guards → {r['snapshot']['at']} за {time.monotonic() - t0:.2f} с")


def slow(ns):
    time.sleep(1.5)
    return {n: True for n in ns}


t0 = time.monotonic()
try:
    r = eng.step("x", snap, "btn:go", guards=slow)
    c = r["snapshot"]["at"]
except EngineError as e:
    c = f"EngineError {e}"
print(f"c) guards 1.5 с при timeout 1 с → {c}, step занял {time.monotonic() - t0:.2f} с "
      f"(срок вызова не ограничивает step целиком)")
eng.close()

# d) close во время активных вызовов
eng = Engine(ENGINE, FEATURES, processes=2, timeout=1.0)
stop = time.monotonic() + 1.0
res = {"ok": 0, "err": 0, "other": 0}
lk = threading.Lock()


def worker(i):
    while time.monotonic() < stop:
        try:
            eng.start(f"k{i}")
            k = "ok"
        except EngineError:
            k = "err"
        except Exception:  # noqa: BLE001
            k = "other"
        with lk:
            res[k] += 1


ts = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
for t in ts:
    t.start()
time.sleep(0.3)
t0 = time.monotonic()
eng.close()
close_took = time.monotonic() - t0
for t in ts:
    t.join(10)
time.sleep(0.3)
print(f"d) close() среди 8 активных потоков: close {close_took:.2f} с, зависло {sum(t.is_alive() for t in ts)}, "
      f"исходы {res}; живых процессов движка после close и остановки потоков: {len(children())}")
for p in eng._procs:
    p.close()   # прибрать воскрешённые процессы (PC-02), чтобы не мешали e)

# e) close во время guards
eng = Engine(ENGINE, FEATURES, processes=1, timeout=1.0)
snap = eng.start("x")["snapshot"]


def closing(ns):
    eng.close()
    return {n: True for n in ns}


try:
    r = eng.step("x", snap, "btn:go", guards=closing)
    e_ = f"ответ {r['snapshot']['at']}"
except EngineError as e:
    e_ = f"EngineError: {e}"
time.sleep(0.3)
print(f"e) close() внутри guards → {e_}; живых процессов движка после: {len(children())}")
for p in eng._procs:
    p.close()

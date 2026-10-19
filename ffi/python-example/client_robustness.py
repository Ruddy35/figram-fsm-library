"""Устойчивость клиента: то, чего не ловят client_faults.py и client_contract.py
(аудит python-client). Подставные «движки» — тестовые двойники (sh), плюс
настоящий движок.

    python3 client_robustness.py ENGINE pizza.bot.json features.bot.json

R1  ошибка, обнаружимая сразу (невалидный JSON, чужой id, обрыв вывода,
    падение, нарушение формы, закрытый stdout), будит всех ожидающих сразу,
    а не по их собственному сроку
R2  300 циклов «сбой → замена»: не растут потоки, дескрипторы, потомки,
    зомби; close() исправного пула мгновенный (stdin закрывается)
R3  после сбоя таблица ожидающих пуста; вызов неисправного процесса —
    ошибка сразу, без ожидания срока
R4  поздний ответ медленного движка: ошибка по сроку, следующий запрос
    получает свой ответ; движок под SIGSTOP: ошибка по сроку, процесс собран
R5  строка с одиночным суррогатом: EngineError, таблица ожидающих не растёт
R6  после close(): EngineError, новых процессов нет (и при close посреди
    активных вызовов)
R7  бинарника нет при замене процесса: EngineError
R8  снимок, который движок не разберёт на уровне текста (ключи 1 и "1",
    True и "true", вложенность > 256), — EngineError только этому вызову:
    процесс не перезапускается, запросы соседей проходят
R9  SIGKILL группе процессов не отправляется по уже собранному pid
"""

import os
import shutil
import signal
import stat
import sys
import tempfile
import threading
import time

import figram_client
from figram_client import Engine, EngineError

engine_bin, pizza, features = sys.argv[1], sys.argv[2], sys.argv[3]
ok = True


def report(good, text):
    global ok
    ok &= bool(good)
    print(f"{'ok ' if good else 'НЕТ'} {text}", flush=True)


def children():
    me, out = os.getpid(), []
    for d in os.listdir("/proc"):
        if d.isdigit():
            try:
                with open(f"/proc/{d}/stat") as f:
                    rest = f.read().rsplit(")", 1)[1].split()
            except OSError:
                continue
            if int(rest[1]) == me:
                out.append(rest[0])
    return out


def wait_children(n, limit=3.0):
    t0 = time.monotonic()
    while len(children()) > n and time.monotonic() - t0 < limit:
        time.sleep(0.02)
    return children()


def resources():
    return (threading.active_count(), len(os.listdir("/proc/self/task")),
            len(os.listdir("/proc/self/fd")))


def parallel(fn, n):
    res, times, lk = [], [], threading.Lock()

    def w(i):
        t = time.monotonic()
        try:
            fn(i)
            r = "ответ"
        except EngineError:
            r = "ошибка"
        except Exception as e:  # noqa: BLE001
            r = type(e).__name__
        with lk:
            res.append(r)
            times.append(time.monotonic() - t)

    ts = [threading.Thread(target=w, args=(i,)) for i in range(n)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(30)
    return res, times, sum(t.is_alive() for t in ts)


tmp = tempfile.mkdtemp()


def fake(body):
    path = os.path.join(tmp, f"fake{len(os.listdir(tmp))}.sh")
    with open(path, "w") as f:
        f.write("#!/bin/sh\n" + body + "\n")
    os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)
    return path


RESP = '{"id":1,"snapshot":{"at":"a"},"actions":[]}'
ECHO = ('while read l; do id=$(echo "$l" | sed \'s/^{"id":\\([0-9]*\\).*/\\1/\'); '
        'echo "{\\"id\\":$id,\\"snapshot\\":{\\"at\\":\\"a\\"},\\"actions\\":[]}"; done')

# R1: мгновенно обнаружимые сбои будят всех сразу (срок 6 с, порог 1.5 с).
INSTANT = {
    "невалидный JSON": 'read l; echo "{oops"; sleep 30',
    "неизвестный id": 'read l; echo \'{"id": 999, "snapshot": {"at": "a"}, "actions": []}\'; sleep 30',
    "обрыв вывода": 'read l; exec 1>&-; sleep 30',
    "падение": 'read l; kill -SEGV $$',
    "seq = true": 'read l; echo \'{"id": 1, "snapshot": {"at": "a", "seq": true}, "actions": []}\'; sleep 30',
    "stdout закрыт сразу": 'exec 1>&-; while read l; do :; done',
}
for name, body in INSTANT.items():
    eng = Engine(fake(body), pizza, timeout=6.0)
    res, times, hung = parallel(lambda i: eng.start("u"), 8)
    eng.close()
    report(res.count("ошибка") == 8 and hung == 0 and max(times) < 1.5,
           f"R1 {name}: ошибок {res.count('ошибка')} из 8, самый долгий {max(times):.2f} с (срок 6 с)")

# R2: ресурсы после многих циклов «сбой → замена».
eng = Engine(fake('read l; kill -SEGV $$'), pizza, timeout=5.0)
base = resources()
done = 0
for _ in range(300):
    t0 = time.monotonic()
    parallel(lambda i: eng.start("u"), 4)
    done += 1
    if time.monotonic() - t0 > 2.5:   # сбой обнаруживается сразу; не ждать 300 × срок
        break
eng.close()
left = wait_children(0)
time.sleep(0.2)
end = resources()
report(done == 300 and end[0] <= base[0] + 1 and end[1] <= base[1] + 1 and end[2] <= base[2] + 2 and not left,
       f"R2 {done} из 300 циклов сбоя (цикл < 2.5 с): потоки Python {base[0]}→{end[0]}, потоки ОС {base[1]}→{end[1]}, "
       f"fd {base[2]}→{end[2]}, потомков после close {len(left)}")
eng = Engine(engine_bin, pizza, processes=2, timeout=5.0)
eng.start("a"), eng.start("b")
t0 = time.monotonic()
eng.close()
took = time.monotonic() - t0
report(took < 1.0 and not wait_children(0, 1.0), f"R2 close() исправного пула из 2 процессов: {took:.2f} с (срок 5 с)")

# R3: таблица ожидающих после сбоя; вызов неисправного процесса.
eng = Engine(fake('while read l; do :; done'), pizza, timeout=6.0)
p = eng._procs[0]
th = [threading.Thread(target=lambda: parallel(lambda i: p.call({"op": "start"}), 4))]
th[0].start()
time.sleep(0.3)
p._fail("внедрённый сбой")
th[0].join(10)
t0 = time.monotonic()
try:
    p.call({"op": "start"})
    r = "ответ"
except EngineError:
    r = "ошибка"
took = time.monotonic() - t0
report(len(p._pending) == 0 and r == "ошибка" and took < 0.5,
       f"R3 после сбоя: ожидающих {len(p._pending)}, вызов неисправного процесса — {r} за {took:.2f} с")
eng.close()

# R4: поздний ответ и SIGSTOP.
late = os.path.join(tmp, "late-once")   # медленный только первый процесс
eng = Engine(fake(f"if [ ! -e {late} ]; then touch {late}; read l; sleep 3; echo '{RESP}'; fi; " + ECHO),
             pizza, timeout=1.0)
t0 = time.monotonic()
try:
    eng.start("u")
    first = "ответ"
except EngineError:
    first = "ошибка"
took = time.monotonic() - t0
try:
    second = eng.start("u")["snapshot"]["at"]
except EngineError as e:
    second = f"ошибка {e}"
eng.close()
report(first == "ошибка" and 0.9 < took < 2.0 and second == "a",
       f"R4 поздний ответ: первый вызов — {first} за {took:.2f} с, следующий — {second}")
eng = Engine(engine_bin, pizza, timeout=1.0)
pid = eng._procs[0]._p.pid
os.kill(pid, signal.SIGSTOP)
res, times, hung = parallel(lambda i: eng.start("u"), 4)
gone = not os.path.exists(f"/proc/{pid}") or not wait_children(0, 2.0)
eng.close()
report(res.count("ошибка") == 4 and max(times) < 2.0 and gone,
       f"R4 SIGSTOP: ошибок {res.count('ошибка')} из 4, самый долгий {max(times):.2f} с, процесс собран {gone}")

# R5: одиночный суррогат.
eng = Engine(engine_bin, pizza, timeout=5.0)
p = eng._procs[0]
kinds = {}
for i in range(200):
    try:
        eng.step("u", {"at": "menu"} if i % 2 else {"at": "menu\udc80"}, "btn:\ud83d")
        k = "ответ"
    except EngineError:
        k = "EngineError"
    except Exception as e:  # noqa: BLE001
        k = type(e).__name__
    kinds[k] = kinds.get(k, 0) + 1
alive = p.alive and "snapshot" in eng.start("u")
report(kinds == {"EngineError": 200} and len(p._pending) == 0 and alive,
       f"R5 200 запросов с одиночным суррогатом: {kinds}, ожидающих {len(p._pending)}, процесс жив {alive}")
eng.close()

# R6: после close.
wait_children(0)
eng = Engine(engine_bin, pizza, processes=2, timeout=2.0)
eng.start("a")
eng.close()
time.sleep(0.2)
res = []
for key in "abcd":
    try:
        eng.start(key)
        res.append("ответ")
    except EngineError:
        res.append("ошибка")
left = wait_children(0, 1.0)
report(res == ["ошибка"] * 4 and not left, f"R6 вызовы после close(): {res}, живых процессов {len(left)}")
eng = Engine(engine_bin, pizza, processes=2, timeout=2.0)
stop = time.monotonic() + 0.8


def busy(i):
    while time.monotonic() < stop:
        try:
            eng.start(f"k{i}")
        except EngineError:
            pass


ths = [threading.Thread(target=busy, args=(i,)) for i in range(4)]
for t in ths:
    t.start()
time.sleep(0.3)
eng.close()
for t in ths:
    t.join(10)
left = wait_children(0, 1.0)
report(not left and not any(t.is_alive() for t in ths),
       f"R6 close() посреди вызовов из 4 потоков: живых процессов после {len(left)}")
for p in eng._procs:   # прибрать, если тест красный
    p.close()

# R7: бинарника нет при замене процесса.
copy = os.path.join(tmp, "engine-copy")
shutil.copy(engine_bin, copy)
eng = Engine(copy, pizza, timeout=2.0)
os.remove(copy)
eng._procs[0]._fail("внедрённый сбой")
kinds = {}
for _ in range(3):
    try:
        eng.start("u")
        k = "ответ"
    except EngineError:
        k = "EngineError"
    except Exception as e:  # noqa: BLE001
        k = type(e).__name__
    kinds[k] = kinds.get(k, 0) + 1
eng.close()
report(kinds == {"EngineError": 3}, f"R7 замена процесса без бинарника: {kinds}")

# R8: строка, которую движок не разберёт (ответ id:null), не роняет соседей.
deep = {}
cur = deep
for _ in range(300):
    cur["a"] = {}
    cur = cur["a"]
BAD = {"ключи 1 и \"1\"": {"at": "menu", "resume": {1: "menu", "1": "menu"}},
       "ключи True и \"true\"": {"at": "menu", "timers": {True: 1, "true": 2}},
       "вложенность 300": {"at": "menu", "x": deep}}
for name, snap in BAD.items():
    eng = Engine(engine_bin, pizza, processes=1, timeout=5.0)
    pid0 = eng._procs[0]._p.pid
    halt = threading.Event()
    errs = [0, 0]

    def neighbour(i):
        s = eng.start(f"n{i}")["snapshot"]
        while not halt.is_set():
            try:
                eng.step(f"n{i}", s, "btn:order")
                errs[1] += 1
            except EngineError:
                errs[0] += 1

    ths = [threading.Thread(target=neighbour, args=(i,)) for i in range(4)]
    for t in ths:
        t.start()
    time.sleep(0.2)
    kinds = {}
    for _ in range(5):
        try:
            eng.step("evil", snap, "btn:x")
            k = "ответ"
        except EngineError:
            k = "EngineError"
        except Exception as e:  # noqa: BLE001
            k = type(e).__name__
        kinds[k] = kinds.get(k, 0) + 1
        time.sleep(0.05)
    halt.set()
    for t in ths:
        t.join(10)
    same = eng._procs[0]._p.pid == pid0 and eng._procs[0].alive
    eng.close()
    report(kinds == {"EngineError": 5} and errs[0] == 0 and same,
           f"R8 {name}: плохой вызов {kinds}; соседи: ошибок {errs[0]}, ответов {errs[1]}; процесс тот же {same}")

# R9: killpg только по ещё не собранному pid.
calls = [0, 0]
lk = threading.Lock()
real_killpg = os.killpg


def spy(pid, sig):
    with lk:
        calls[0] += 1
        if not os.path.exists(f"/proc/{pid}"):
            calls[1] += 1
    return real_killpg(pid, sig)


figram_client.os.killpg = spy
try:
    eng = Engine(fake('read l; kill -SEGV $$'), pizza, timeout=2.0)
    for _ in range(200):
        t0 = time.monotonic()
        parallel(lambda i: eng.start("u"), 8)
        if time.monotonic() - t0 > 1.5:   # сбой обнаруживается сразу; не ждать 200 × срок
            calls[1] += 1000
            break
    eng.close()
    eng = Engine(engine_bin, pizza, processes=2, timeout=2.0)
    for _ in range(50):
        eng.start("a")
        eng.start("b")
    eng.close()
finally:
    figram_client.os.killpg = real_killpg
report(calls[1] == 0 and calls[0] > 0,
       f"R9 killpg: вызовов {calls[0]}, из них по уже собранному (свободному) pid {calls[1]}")

shutil.rmtree(tmp, ignore_errors=True)
print("клиент устойчив" if ok else "НАРУШЕНИЕ УСТОЙЧИВОСТИ КЛИЕНТА")
sys.exit(0 if ok else 1)

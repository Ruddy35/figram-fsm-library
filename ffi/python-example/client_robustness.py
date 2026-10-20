"""Устойчивость клиента: то, чего не ловят client_faults.py и client_contract.py
(аудит python-client). Подставные «движки» — тестовые двойники (sh, Python),
плюс настоящий движок. Клиент при запуске процесса отправляет hello с id 0 и
ждёт ответа: sh-двойники сначала отвечают на него (HELLO), затем сбоят.

    python3 client_robustness.py ENGINE pizza.bot.json features.bot.json

R1  ошибка, обнаружимая сразу (невалидный JSON, чужой id, обрыв вывода,
    падение, нарушение формы, закрытый stdout), будит всех ожидающих сразу,
    а не по их собственному сроку
R2  300 циклов «сбой → замена»: не растут потоки, дескрипторы, потомки,
    зомби; close() исправного пула мгновенный (stdin закрывается)
R3  после сбоя таблица ожидающих пуста; вызов неисправного процесса —
    ошибка сразу, без ожидания срока
R4  поздний ответ медленного движка: ошибка по сроку, процесс не убит, поздний
    ответ отброшен, следующий запрос получает свой ответ от того же процесса;
    движок под SIGSTOP: ошибка по сроку, процесс собран по hang_timeout
R5  строка с одиночным суррогатом: EngineError, таблица ожидающих не растёт
R6  после close(): EngineError, новых процессов нет (и при close посреди
    активных вызовов)
R7  бинарника нет при замене процесса: EngineError
R8  снимок, который движок не разберёт на уровне текста (ключи 1 и "1",
    True и "true", вложенность > 256), — EngineError только этому вызову:
    процесс не перезапускается, запросы соседей проходят
R9  SIGKILL группе процессов не отправляется по уже собранному pid

Регрессии fix/client (двойники на Python — pyfake, плюс настоящий движок):
R10 (I-01) фаза запуска: загрузка дольше timeout не мешает (двойник и
    настоящий движок с медленным документом); после сбоя прогретого процесса
    замена ждёт загрузки; start_timeout; срок вызова — от готовности;
    неизвестная версия протокола — отказ запуска
R11 (I-04) таймаут проваливает только свой вызов: общий процесс не убит,
    поздний ответ отброшен, соседи получают свои ответы (двойник и настоящий
    движок со «ядовитым» снимком); зависший процесс заменяется по hang_timeout
R12 (I-03) ответ-ошибка с id:null завершает только самый старый запрос
    (новый протокол с code и старый без), процесс не убит; остальные
    нарушения по-прежнему убивают
R13 (I-07) хвост stderr и код выхода в EngineError (ошибка документа);
    экспоненциальная пауза между перезапусками; сброс паузы после работы
R14 (I-08) docstring: порядок событий обеспечивает хост (HOST.md §1.1)
R15 (I-10) длина строки ответа ограничена (по умолчанию и настраиваемо)
R16 (I-11) close() пула из N зависших процессов — один общий срок
R17 (I-12) общий срок Engine.step (два запроса + функция условий)
R18 (I-13) ответ строго UTF-8: суррогаты (байтами и экранированные) — отказ
R19 EngineError.code / retryable; ответ-ошибка неверной формы — нарушение
R20 start(key, seq): поле seq уходит в запрос только когда задано
"""

import json
import os
import shutil
import signal
import stat
import subprocess
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


# Клиент сначала отправляет {"id":0,"op":"hello"} и ждёт ответа (фаза запуска):
# двойник, который должен сбоить на ЗАПРОСЕ, сначала отвечает на hello.
HELLO = 'read l; echo \'{"id":0,"protocol":1,"states":1}\'; '
RESP = '{"id":1,"snapshot":{"at":"a"},"actions":[]}'
# Эхо: на hello — ответ hello, на остальное — результат с тем же id.
ECHO = (r'''while read l; do id=$(echo "$l" | sed 's/^{"id":\([0-9]*\).*/\1/'); '''
        r'''case "$l" in *'"op":"hello"'*) echo "{\"id\":$id,\"protocol\":1,\"states\":1}";; '''
        r'''*) echo "{\"id\":$id,\"snapshot\":{\"at\":\"a\"},\"actions\":[]}";; esac; done''')

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
    eng = Engine(fake(HELLO + body), pizza, timeout=6.0)
    res, times, hung = parallel(lambda i: eng.start("u"), 8)
    eng.close()
    report(res.count("ошибка") == 8 and hung == 0 and max(times) < 1.5,
           f"R1 {name}: ошибок {res.count('ошибка')} из 8, самый долгий {max(times):.2f} с (срок 6 с)")

# R2: ресурсы после многих циклов «сбой → замена» (пауза перезапуска отключена,
# иначе замен было бы несколько, а не 300; сама пауза — R13).
eng = Engine(fake(HELLO + 'read l; kill -SEGV $$'), pizza, timeout=5.0, restart_backoff=(0, 0))
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
eng = Engine(fake(HELLO + 'while read l; do :; done'), pizza, timeout=6.0)
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

# R4: поздний ответ и SIGSTOP. Таймаут проваливает только свой вызов (I-04):
# процесс не убивается, поздний ответ отбрасывается, следующий запрос получает
# свой ответ от того же процесса. Зависший (SIGSTOP) процесс заменяется по
# hang_timeout.
late = os.path.join(tmp, "late-once")   # медленный только первый запрос
eng = Engine(fake(HELLO + f"if [ ! -e {late} ]; then touch {late}; read l; sleep 3; echo '{RESP}'; fi; " + ECHO),
             pizza, timeout=1.0)
pid0 = eng._procs[0]._p.pid
t0 = time.monotonic()
try:
    eng.start("u")
    first = "ответ"
except EngineError:
    first = "ошибка"
took = time.monotonic() - t0
time.sleep(max(0.0, t0 + 3.5 - time.monotonic()))   # поздний ответ уже пришёл
try:
    second = eng.start("u")["snapshot"]["at"]
except EngineError as e:
    second = f"ошибка {e}"
same = eng._procs[0].alive and eng._procs[0]._p.pid == pid0
eng.close()
report(first == "ошибка" and 0.9 < took < 2.0 and second == "a" and same,
       f"R4 поздний ответ: первый вызов — {first} за {took:.2f} с, следующий — {second}, процесс тот же {same}")
eng = Engine(engine_bin, pizza, timeout=1.0, hang_timeout=1.5)
pid = eng._procs[0]._p.pid
os.kill(pid, signal.SIGSTOP)
res, times, hung = parallel(lambda i: eng.start("u"), 4)
gone = not os.path.exists(f"/proc/{pid}") or not wait_children(0, 3.0)
eng.close()
report(res.count("ошибка") == 4 and max(times) < 2.0 and gone,
       f"R4 SIGSTOP: ошибок {res.count('ошибка')} из 4, самый долгий {max(times):.2f} с, "
       f"процесс собран по hang_timeout 1.5 с {gone}")

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

# R6 (гонка, детерминированно): вызов прошёл проверку «закрыт?» и ждёт замка
# замены процесса, в этот момент close() завершается целиком. Замок-обёртка
# придерживает такой вызов до конца close().
eng = Engine(engine_bin, pizza, processes=1, timeout=2.0)
real_swap = eng._swap
at_lock, closed = threading.Event(), threading.Event()


class HoldingLock:
    def __enter__(self):
        if threading.current_thread() is not threading.main_thread():
            at_lock.set()
            closed.wait(5)
        return real_swap.__enter__()

    def __exit__(self, *exc):
        return real_swap.__exit__(*exc)


eng._swap = HoldingLock()
eng._procs[0]._fail("внедрённый сбой")   # чтобы вызов пошёл в замену процесса
out = []


def racer():
    try:
        eng.start("u")
        out.append("ответ")
    except EngineError:
        out.append("ошибка")


t = threading.Thread(target=racer)
t.start()
at_lock.wait(5)
eng.close()
closed.set()
t.join(10)
left = wait_children(0, 1.0)
report(out == ["ошибка"] and not left,
       f"R6 вызов, ждавший замка во время close(): {out}, живых процессов {len(left)}")
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
    eng = Engine(fake(HELLO + 'read l; kill -SEGV $$'), pizza, timeout=2.0, restart_backoff=(0, 0))
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

# ---------------------------------------------------------------------------
# R10–R20: регрессии fix/client. pyfake — ТЕСТОВЫЕ ДВОЙНИКИ движка на Python
# (не настоящий figram-engine): отвечают на hello и на запросы по сценарию,
# поэтому работают и с клиентом, который hello не отправляет (fix/base) —
# на нём эти проверки красные по существу, а не из-за рукопожатия.

PYHEAD = f"""#!{sys.executable} -I
# ТЕСТОВЫЙ ДВОЙНИК figram-engine (client_robustness.py), не настоящий движок.
import json, os, signal, sys, time
def out(o):
    sys.stdout.write(json.dumps(o, ensure_ascii=False) + "\\n"); sys.stdout.flush()
def raw(b):
    sys.stdout.buffer.write(b + b"\\n"); sys.stdout.buffer.flush()
def hello(rid, protocol=1, **extra):
    out({{"id": rid, "protocol": protocol, "states": 1, **extra}})
def result(rid, at="a"):
    out({{"id": rid, "snapshot": {{"at": at}}, "actions": []}})
def requests():
    for line in sys.stdin:
        r = json.loads(line)
        if r.get("op") == "hello":
            hello(r["id"])
            continue
        yield r
"""


def pyfake(body):
    path = os.path.join(tmp, f"pyfake{len(os.listdir(tmp))}.py")
    with open(path, "w") as f:
        f.write(PYHEAD + body + "\n")
    os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)
    return path


def counter(name):
    """Счётчик запусков двойника: (путь, строка кода для начала двойника)."""
    path = os.path.join(tmp, name)
    return path, f"open({path!r}, 'a').write(str(os.getpid()) + '\\n')\n"


def launches(path):
    try:
        with open(path) as f:
            return len(f.read().split())
    except FileNotFoundError:
        return 0


def attempt(fn):
    try:
        return fn()
    except EngineError:
        return "ошибка"
    except Exception as e:  # noqa: BLE001
        return type(e).__name__


def kill_alive(eng, i=0):
    """SIGKILL группе процесса слота, только если клиент считает его живым
    (pid ещё не собран), и дождаться, пока клиент заметит сбой (EOF вывода)."""
    p = eng._procs[i]
    if p.alive:
        try:
            os.killpg(p._p.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    t0 = time.monotonic()
    while p.alive and time.monotonic() - t0 < 5:
        time.sleep(0.01)


def hwm_mb():
    with open("/proc/self/status") as f:
        for line in f:
            if line.startswith("VmHWM:"):
                return int(line.split()[1]) / 1024
    return 0.0


def unique_doc(n):
    """Документ, который грузится сверхлинейно долго (cross-verify unique-N):
    у каждого сообщения своя кнопка."""
    names = [f"m{i}" for i in range(n)]
    ch = {m: {"on": {f"btn:b{i}": [{"to": names[(i + 1) % n]}]}} for i, m in enumerate(names)}
    return {"figram": 1, "bot": {"initial": names[0], "children": ch}}


def neighbours(eng, n, check):
    """n потоков шлют свои шаги, пока не выставлен stop; check(i, k) → bool
    (ответ свой и верный). Счётчики {ok, err, wrong, late_ok}: late_ok — верные
    ответы после момента stats['after'], если он задан."""
    stats = {"ok": 0, "err": 0, "wrong": 0, "late_ok": 0, "after": None}
    lk = threading.Lock()
    stop = threading.Event()

    def run(i):
        k = 0
        while not stop.is_set():
            k += 1
            try:
                key = "ok" if check(i, k) else "wrong"
            except EngineError:
                key = "err"
            with lk:
                stats[key] += 1
                if key == "ok" and stats["after"] is not None and time.monotonic() > stats["after"]:
                    stats["late_ok"] += 1
            time.sleep(0.005)

    ths = [threading.Thread(target=run, args=(i,)) for i in range(n)]
    for t in ths:
        t.start()
    return ths, stats, stop


def drained(eng, key, limit=60.0):
    """Дождаться, пока процесс снова отвечает в срок (очередь медленных
    запросов выполнена): вызывать start, пока не ответит."""
    t0 = time.monotonic()
    while time.monotonic() - t0 < limit:
        if attempt(lambda: eng.start(key)) != "ошибка":
            return True
    return False


def finish(ths, st, stop, eng, settle=1.0):
    """Медленные запросы выполнены → ещё settle с работы соседей → стоп."""
    ok = drained(eng, "drain")
    st["after"] = time.monotonic()
    time.sleep(settle)
    stop.set()
    for t in ths:
        t.join(30)
    return ok


def r10_startup():
    """I-01: загрузка документа дольше timeout; замена после сбоя ждёт загрузки."""
    cnt, line = counter("r10.count")
    slow = pyfake(line + "time.sleep(2.5)\nfor r in requests():\n    result(r['id'], 'loaded')\n")
    t0 = time.monotonic()
    eng = Engine(slow, pizza, timeout=1.0)
    try:
        res = [attempt(lambda: eng.start("u")["snapshot"]["at"]) for _ in range(3)]
        report(res == ["loaded"] * 3 and launches(cnt) == 1,
               f"R10 загрузка 2.5 с при timeout 1 с (двойник): ответы {res}, запусков движка {launches(cnt)}, "
               f"{time.monotonic() - t0:.1f} с")
        kill_alive(eng)                     # сбой прогретого процесса (cross-verify C3, вариант C)
        t1 = time.monotonic()
        res = [attempt(lambda: eng.start("u")["snapshot"]["at"]) for _ in range(3)]
        report(res == ["loaded"] * 3 and launches(cnt) == 2,
               f"R10 после сбоя прогретого процесса замена ждёт загрузки: ответы {res}, "
               f"запусков {launches(cnt)}, {time.monotonic() - t1:.1f} с")
    finally:
        eng.close()


def r10_real():
    """I-01 на настоящем движке: документ с уникальными кнопками, timeout = загрузка / 4."""
    load, doc, n = 0.0, None, 0
    for n in (360, 600):
        doc = os.path.join(tmp, f"unique-{n}.bot.json")
        with open(doc, "w") as f:
            json.dump(unique_doc(n), f)
        t0 = time.monotonic()
        subprocess.run([engine_bin, doc], input=b'{"id":1,"op":"hello"}\n', capture_output=True, timeout=300)
        load = time.monotonic() - t0
        if load >= 0.8:
            break
    if load < 0.8:
        report(True, f"R10 настоящий движок: unique-{n} грузится {load:.2f} с — быстрее порога проверки, пропущено")
        return
    timeout = max(0.1, load / 4)
    eng = Engine(engine_bin, doc, timeout=timeout)
    try:
        r1 = attempt(lambda: eng.start("u")["snapshot"]["at"])
        kill_alive(eng)
        r2 = attempt(lambda: eng.start("u")["snapshot"]["at"])
    finally:
        eng.close()
    report(r1 == "m0" and r2 == "m0",
           f"R10 настоящий движок, unique-{n} грузится {load:.1f} с, timeout {timeout:.2f} с: "
           f"первый вызов — {r1}, после сбоя процесса — {r2}")


def r10_options():
    """I-01: start_timeout, срок вызова от готовности, версия протокола в hello."""
    never = pyfake("time.sleep(60)\n")
    t0 = time.monotonic()
    try:
        Engine(never, pizza, timeout=0.5, start_timeout=1.0).close()
        r = "создан"
    except EngineError as e:
        r = f"EngineError: {e}"
    took = time.monotonic() - t0
    left = wait_children(0, 3.0)
    report(r.startswith("EngineError") and "hello" in r and 0.9 < took < 3.0 and not left,
           f"R10 start_timeout 1 с, движок не отвечает на hello: «{r[:80]}», {took:.1f} с, потомков {len(left)}")

    slow = pyfake("time.sleep(2.0)\nfor r in requests():\n    result(r['id'], 'loaded')\n")
    t0 = time.monotonic()
    eng = Engine(slow, pizza, timeout=0.5, wait_ready=False)
    created = time.monotonic() - t0
    try:
        got = {}
        th = threading.Thread(target=lambda: got.update(
            step=attempt(lambda: eng.step("v", {"at": "a"}, "btn:x")["snapshot"]["at"])))
        th.start()
        r = attempt(lambda: eng.start("u")["snapshot"]["at"])
        took = time.monotonic() - t0
        th.join(10)
    finally:
        eng.close()
    report(created < 0.5 and r == "loaded" and got.get("step") == "loaded" and took > 1.5,
           f"R10 wait_ready=False: конструктор {created:.2f} с, вызовы во время загрузки — start {r}, "
           f"step {got.get('step')} через {took:.1f} с (timeout 0.5 с и срок шага — от готовности)")

    for what, hello_args, expect_ok in (("протокол 2", "protocol=2", False),
                                        ("поле mutant (тестовый движок)", "mutant='x'", True)):
        fake = pyfake("for line in sys.stdin:\n    r = json.loads(line)\n"
                      f"    if r.get('op') == 'hello':\n        hello(r['id'], {hello_args})\n"
                      "    else:\n        result(r['id'], 'ok')\n")
        try:
            eng = Engine(fake, pizza, timeout=2.0)
            r = attempt(lambda: eng.start("u")["snapshot"]["at"])
            eng.close()
        except EngineError as e:
            r = f"EngineError: {e}"
        good = r == "ok" if expect_ok else (r.startswith("EngineError") and "протокол" in r)
        report(good, f"R10 hello с {what}: {r[:100]}")


def r11_slow_request():
    """I-04: медленный запрос + повтор хоста не убивают общий процесс."""
    cnt, line = counter("r11.count")
    fake = pyfake(line + "for r in requests():\n"
                  "    if r.get('snapshot', {}).get('at') == 'slow':\n        time.sleep(1.5)\n"
                  "    result(r['id'], r['op'] + ':' + r.get('event', ''))\n")
    eng = Engine(fake, pizza, timeout=0.6)
    try:
        pid0 = eng._procs[0]._p.pid
        ths, st, stop = neighbours(eng, 4, lambda i, k: eng.step(
            f"n{i}", {"at": "x"}, f"btn:n{i}-{k}")["snapshot"]["at"] == f"step:btn:n{i}-{k}")
        time.sleep(0.2)
        poison = [attempt(lambda: eng.step("evil", {"at": "slow"}, "btn:p")) for _ in range(2)]
        finish(ths, st, stop, eng)
        p = eng._procs[0]
        same = p.alive and p._p.pid == pid0
        final = attempt(lambda: eng.start("u")["snapshot"]["at"])
    finally:
        eng.close()
    report(poison == ["ошибка"] * 2 and st["wrong"] == 0 and st["late_ok"] > 0 and same
           and launches(cnt) == 1 and final == "start:",
           f"R11 медленный запрос (1.5 с при timeout 0.6 с) дважды: {poison}; соседи: ответов {st['ok']} "
           f"(после медленных {st['late_ok']}), ошибок {st['err']}, чужих ответов {st['wrong']}; "
           f"процесс тот же {same}, запусков {launches(cnt)}")


def r11_real():
    """I-04 на настоящем движке: «ядовитый» снимок (resume из K записей) разбирается долго."""
    took, K = 0.0, 0
    for K in (20000, 30000):
        snap = {"at": "menu", "resume": {f"s{i}": "menu" for i in range(K)}}
        req = json.dumps({"id": 2, "op": "step", "snapshot": snap, "event": "btn:x"}).encode()
        p = subprocess.Popen([engine_bin, pizza], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL)
        p.stdin.write(b'{"id":1,"op":"hello"}\n')
        p.stdin.flush()
        p.stdout.readline()
        t0 = time.monotonic()
        p.stdin.write(req + b"\n")
        p.stdin.flush()
        p.stdout.readline()
        took = time.monotonic() - t0
        p.stdin.close()
        p.wait()
        if took >= 0.9:
            break
    if took < 0.9:
        report(True, f"R11 настоящий движок: снимок из {K} записей разбирается {took:.2f} с — быстро, пропущено")
        return
    timeout = took / 3
    eng = Engine(engine_bin, pizza, timeout=timeout)
    try:
        pid0 = eng._procs[0]._p.pid
        s0 = eng.start("n")["snapshot"]
        want = eng.step("n", s0, "btn:order")["snapshot"]
        ths, st, stop = neighbours(eng, 4, lambda i, k: eng.step(f"n{i}", s0, "btn:order")["snapshot"] == want)
        time.sleep(0.1)
        poison = [attempt(lambda: eng.step("evil", snap, "btn:x")) for _ in range(2)]
        finish(ths, st, stop, eng)
        p = eng._procs[0]
        same = p.alive and p._p.pid == pid0
    finally:
        eng.close()
    report(poison == ["ошибка"] * 2 and st["wrong"] == 0 and st["late_ok"] > 0 and same,
           f"R11 настоящий движок, снимок из {K} записей ({took:.1f} с) при timeout {timeout:.2f} с дважды: "
           f"{poison}; соседи: ответов {st['ok']} (после {st['late_ok']}), ошибок {st['err']}, "
           f"чужих {st['wrong']}; процесс тот же {same}")


def r11_hang():
    """I-04: процесс, который молчит при неотвеченных запросах, заменяется по hang_timeout."""
    cnt, line = counter("r11h.count")
    # Отвечает только на hello при запуске, дальше читает и молчит.
    fake = pyfake(line + "hello(json.loads(sys.stdin.readline())['id'])\nfor l in sys.stdin:\n    pass\n")
    eng = Engine(fake, pizza, timeout=0.5, hang_timeout=1.5)
    try:
        p = eng._procs[0]
        pid = p._p.pid
        t0 = time.monotonic()
        r1 = attempt(lambda: eng.start("u"))
        while p.alive and time.monotonic() - t0 < 6:
            time.sleep(0.02)
        detected = time.monotonic() - t0
        t1 = time.monotonic()
        while os.path.exists(f"/proc/{pid}") and time.monotonic() - t1 < 3:
            time.sleep(0.02)
        gone = not os.path.exists(f"/proc/{pid}")
        r2 = attempt(lambda: eng.start("u"))
        n = launches(cnt)
        why = p.failure or ""
    finally:
        eng.close()
    report(r1 == "ошибка" and "завис" in why and 1.2 < detected < 4.0 and gone and n == 2 and r2 == "ошибка",
           f"R11 зависший процесс (hang_timeout 1.5 с, timeout 0.5 с): вызов — {r1}, замечен через {detected:.1f} с "
           f"«{why[:60]}», собран {gone}, следующий вызов в новом процессе (запусков {n})")
    # Пропущенный ответ: движок ответил на проверку живости (hello после
    # таймаута), но не на запрос перед ней — порядок ответов нарушен, сбой сразу.
    fake = pyfake("for r in requests():\n    pass\n")
    eng = Engine(fake, pizza, timeout=0.5, hang_timeout=30.0)
    try:
        p = eng._procs[0]
        r1 = attempt(lambda: eng.start("u"))
        t0 = time.monotonic()
        while p.alive and time.monotonic() - t0 < 3:
            time.sleep(0.02)
        why = p.failure or ""
    finally:
        eng.close()
    report(r1 == "ошибка" and "порядок" in why and time.monotonic() - t0 < 3,
           f"R11 движок пропустил ответ на запрос, но ответил на следующий: «{why[:70]}»")


def r12_id_null():
    """I-03: ответ-ошибка с id:null относится к самому старому запросу."""
    for what, reply, code in (("новый протокол", '{"id": None, "error": "строка не разобрана", "code": "parse_error"}',
                               "parse_error"),
                              ("старый протокол", '{"id": None, "error": "строка не разобрана"}', None)):
        cnt, line = counter(f"r12-{code}.count")
        fake = pyfake(line + "for r in requests():\n"
                      f"    if r.get('event') == 'btn:bad':\n        out({reply})\n"
                      "    else:\n        result(r['id'], r['op'] + ':' + r.get('event', ''))\n")
        eng = Engine(fake, pizza, timeout=2.0)
        try:
            pid0 = eng._procs[0]._p.pid
            ths, st, stop = neighbours(eng, 4, lambda i, k: eng.step(
                f"n{i}", {"at": "x"}, f"btn:n{i}-{k}")["snapshot"]["at"] == f"step:btn:n{i}-{k}")
            time.sleep(0.1)
            bad = []
            for _ in range(5):
                try:
                    eng.step("evil", {"at": "x"}, "btn:bad")
                    bad.append("ответ")
                except EngineError as e:
                    bad.append((getattr(e, "code", "?"), getattr(e, "retryable", "?")))
                time.sleep(0.05)
            time.sleep(0.5)
            stop.set()
            for t in ths:
                t.join(10)
            same = eng._procs[0].alive and eng._procs[0]._p.pid == pid0
        finally:
            eng.close()
        report(bad == [(code, False)] * 5 and st["err"] == 0 and st["wrong"] == 0 and st["ok"] > 0 and same
               and launches(cnt) == 1,
               f"R12 id:null ({what}): плохие вызовы {bad[:2]}…; соседи: ответов {st['ok']}, ошибок {st['err']}, "
               f"чужих {st['wrong']}; процесс тот же {same}, запусков {launches(cnt)}")


def r12_real():
    """I-03 на настоящем движке: строка с повторяющимся ключом (проверка клиента
    _check_wire отключена). Старый движок отвечает id:null, новый — с id."""
    saved = figram_client._check_wire
    figram_client._check_wire = lambda x, depth=0: None
    try:
        eng = Engine(engine_bin, pizza, timeout=3.0)
        try:
            pid0 = eng._procs[0]._p.pid
            s0 = eng.start("n")["snapshot"]
            want = eng.step("n", s0, "btn:order")["snapshot"]
            ths, st, stop = neighbours(eng, 4, lambda i, k: eng.step(f"n{i}", s0, "btn:order")["snapshot"] == want)
            time.sleep(0.1)
            bad = []
            for _ in range(5):
                try:
                    eng.step("evil", {"at": "menu", "resume": {1: "menu", "1": "menu"}}, "btn:x")
                    bad.append("ответ")
                except EngineError as e:
                    bad.append(getattr(e, "retryable", "?"))
                time.sleep(0.05)
            time.sleep(0.5)
            stop.set()
            for t in ths:
                t.join(10)
            same = eng._procs[0].alive and eng._procs[0]._p.pid == pid0
        finally:
            eng.close()
    finally:
        figram_client._check_wire = saved
    report(bad == [False] * 5 and st["err"] == 0 and st["wrong"] == 0 and same,
           f"R12 настоящий движок, повторяющийся ключ в снимке: плохие вызовы {bad}; соседи: ответов {st['ok']}, "
           f"ошибок {st['err']}; процесс тот же {same}")


def r12_control():
    """I-03, контроль: прочие ответы без целого id по-прежнему — нарушение протокола."""
    fake = pyfake("for r in requests():\n    out({'id': None, 'snapshot': {'at': 'a'}, 'actions': []})\n")
    eng = Engine(fake, pizza, timeout=2.0)
    try:
        r = attempt(lambda: eng.start("u"))
        dead = not eng._procs[0].alive
    finally:
        eng.close()
    report(r == "ошибка" and dead, f"R12 контроль: результат с id:null — {r}, процесс помечен неисправным {dead}")
    fake = pyfake("for line in sys.stdin:\n    r = json.loads(line)\n    if r.get('op') == 'hello':\n"
                  "        hello(r['id']); time.sleep(0.3); out({'id': None, 'error': 'x'})\n"
                  "    else:\n        result(r['id'])\n")
    eng = Engine(fake, pizza, timeout=2.0)
    try:
        time.sleep(1.0)
        dead = not eng._procs[0].alive
    finally:
        eng.close()
    report(dead, f"R12 контроль: ошибка id:null без ожидающих запросов — процесс неисправен {dead}")


def r13_doc_error():
    """I-07: причина отказа запуска (stderr движка, код выхода) — в тексте EngineError."""
    bad = os.path.join(tmp, "bad.bot.json")
    with open(bad, "w") as f:
        json.dump({"figram": 1, "bot": {"initial": "nowhere", "children": {"a": {}}}}, f)
    msg = ""
    try:
        eng = Engine(engine_bin, bad, timeout=2.0)
        try:
            eng.start("u")
        except EngineError as e:
            msg = str(e)
        finally:
            eng.close()
    except EngineError as e:
        msg = str(e)
    report("ошибка документа" in msg and "код" in msg,
           f"R13 настоящий движок, невалидный документ: «{msg[:140]}»")


def r13_backoff():
    """I-07: экспоненциальная пауза между перезапусками; сброс после работы."""
    cnt, line = counter("r13.count")
    broken = os.path.join(tmp, "r13.broken")
    fake = pyfake(line + f"if os.path.exists({broken!r}):\n"
                  "    sys.stderr.write('ошибка документа: сломан для теста\\n'); sys.stderr.flush(); sys.exit(1)\n"
                  "for r in requests():\n    result(r['id'], 'ok')\n")
    eng = Engine(fake, pizza, timeout=1.0)
    try:
        first = attempt(lambda: eng.start("u")["snapshot"]["at"])
        open(broken, "w").close()
        n0 = launches(cnt)
        kill_alive(eng)
        msgs, lk = [], threading.Lock()
        stop = time.monotonic() + 3.0

        def retry():
            while time.monotonic() < stop:
                try:
                    eng.start("u")
                except EngineError as e:
                    with lk:
                        msgs.append(str(e))
                time.sleep(0.005)

        ths = [threading.Thread(target=retry) for _ in range(4)]
        for t in ths:
            t.start()
        for t in ths:
            t.join(15)
        n = launches(cnt) - n0
        stderr_seen = sum("сломан для теста" in m for m in msgs)
        report(first == "ok" and 2 <= n <= 10 and stderr_seen > 0,
               f"R13 документ сломан после сбоя, 4 потока повторяют 3 с: запусков движка {n} (пауза растёт), "
               f"ошибок {len(msgs)}, из них с хвостом stderr {stderr_seen}")
        os.remove(broken)
        t0 = time.monotonic()
        r = "ошибка"
        while time.monotonic() - t0 < 40:
            r = attempt(lambda: eng.start("u")["snapshot"]["at"])
            if r == "ok":
                break
            time.sleep(0.1)
        back = time.monotonic() - t0
        kill_alive(eng)                   # процесс поработал — пауза сбрасывается
        again = attempt(lambda: eng.start("u")["snapshot"]["at"])
    finally:
        eng.close()
    report(r == "ok" and again == "ok",
           f"R13 документ исправлен: снова отвечает через {back:.1f} с; после сбоя поработавшего процесса "
           f"замена сразу — {again}")


def r14_doc():
    """I-08: docstring не обещает порядок событий, отсылает к блокировке хоста."""
    doc = figram_client.__doc__ or ""
    good = "строго по порядку отправки" not in doc and "HOST.md §1.1" in doc and "блокиров" in doc
    report(good, "R14 docstring: порядок событий пользователя обеспечивает хост блокировкой (HOST.md §1.1)")


def r15_line():
    """I-10: длина строки ответа ограничена."""
    endless = pyfake("for r in requests():\n    chunk = b'x' * 65536\n    while True:\n"
                     "        sys.stdout.buffer.write(chunk)\n")
    h0 = hwm_mb()
    eng = Engine(endless, pizza, timeout=3.0)
    t0 = time.monotonic()
    try:
        r = attempt(lambda: eng.start("u"))
        took = time.monotonic() - t0
    finally:
        eng.close()
    grow = hwm_mb() - h0
    report(r == "ошибка" and took < 2.5 and grow < 100,
           f"R15 бесконечная строка ответа: {r} за {took:.2f} с (срок 3 с), рост пика памяти {grow:.0f} МБ")
    fake = pyfake("for r in requests():\n    result(r['id'], 'a' * (3000 if r.get('event') == 'btn:ok' else 5000))\n")
    eng = Engine(fake, pizza, timeout=2.0, max_line=4096)
    try:
        r1 = attempt(lambda: len(eng.step("u", {"at": "a"}, "btn:ok")["snapshot"]["at"]))
        r2 = attempt(lambda: eng.step("u", {"at": "a"}, "btn:big"))
        dead = not eng._procs[0].alive
    finally:
        eng.close()
    report(r1 == 3000 and r2 == "ошибка" and dead,
           f"R15 max_line=4096: ответ 3000 байт — {r1}, ответ 5000 байт — {r2}, процесс неисправен {dead}")


def r16_close():
    """I-11: close() закрывает процессы одновременно, с одним общим сроком."""
    fake = pyfake("signal.signal(signal.SIGTERM, signal.SIG_IGN)\nline = sys.stdin.readline()\n"
                  "if line:\n    hello(json.loads(line)['id'])\nwhile True:\n    time.sleep(1)\n")
    eng = Engine(fake, pizza, processes=4, timeout=1.0)
    t0 = time.monotonic()
    eng.close()
    took = time.monotonic() - t0
    left = wait_children(0, 2.0)
    report(took < 1.9 and not left,
           f"R16 close() пула из 4 процессов, которые не выходят по EOF: {took:.2f} с (timeout 1 с), "
           f"потомков после {len(left)}")
    # Процесс ещё загружается: вход он прочтёт только после загрузки — close()
    # убивает его сразу, а не ждёт срок.
    slow = pyfake("time.sleep(30)\nfor r in requests():\n    result(r['id'])\n")
    eng = Engine(slow, pizza, processes=2, timeout=2.0, wait_ready=False)
    time.sleep(0.2)
    t0 = time.monotonic()
    eng.close()
    took = time.monotonic() - t0
    left = wait_children(0, 2.0)
    report(took < 0.5 and not left,
           f"R16 close() пула, процессы которого ещё загружаются: {took:.2f} с (timeout 2 с), потомков {len(left)}")


def r17_step():
    """I-12: общий срок Engine.step."""
    eng = Engine(engine_bin, features, timeout=0.5)
    try:
        snap = eng.start("x")["snapshot"]
        p = eng._procs[0]
        sent = [0]
        real = p.call

        def counting(*a, **k):
            sent[0] += 1
            return real(*a, **k)

        p.call = counting

        def slow_guards(names):
            time.sleep(1.3)
            return {n: True for n in names}

        t0 = time.monotonic()
        r = attempt(lambda: eng.step("x", snap, "btn:go", guards=slow_guards)["snapshot"]["at"])
        took = time.monotonic() - t0
    finally:
        eng.close()
    report(r == "ошибка" and sent[0] == 1 and took < 2.0,
           f"R17 функция условий 1.3 с, timeout 0.5 с (step_timeout по умолчанию 1 с): {r} за {took:.1f} с, "
           f"запросов к движку {sent[0]} (второй не отправлен)")
    eng = Engine(engine_bin, features, timeout=0.5, step_timeout=5.0)
    try:
        snap = eng.start("x")["snapshot"]
        r = attempt(lambda: eng.step("x", snap, "btn:go", guards=slow_guards)["snapshot"]["at"])
    finally:
        eng.close()
    report(r == "vip", f"R17 step_timeout=5 с, функция условий 1.3 с при timeout 0.5 с: {r}")
    fake = pyfake("for r in requests():\n    if 'guards' in r:\n        time.sleep(1.5); result(r['id'], 'g')\n"
                  "    else:\n        out({'id': r['id'], 'need': ['c']})\n")
    eng = Engine(fake, pizza, timeout=2.0, step_timeout=1.0)
    try:
        def guards(names):
            time.sleep(0.5)
            return {n: True for n in names}

        t0 = time.monotonic()
        r = attempt(lambda: eng.step("u", {"at": "a"}, "btn:x", guards=guards))
        took = time.monotonic() - t0
    finally:
        eng.close()
    report(r == "ошибка" and took < 1.5,
           f"R17 условия 0.5 с + второй запрос 1.5 с при step_timeout 1 с (timeout 2 с): {r} за {took:.2f} с")


def r18_utf8():
    """I-13: ответ декодируется строго как UTF-8."""
    fake = pyfake(r"""for r in requests():
    ev = r.get("event")
    if ev == "btn:cesu":
        raw(b'{"id":%d,"snapshot":{"at":"\xed\xa0\x80"},"actions":[]}' % r["id"])
    elif ev == "btn:esc":
        raw(b'{"id":%d,"snapshot":{"at":"\\ud800"},"actions":[]}' % r["id"])
    elif ev == "btn:pair":
        raw(b'{"id":%d,"snapshot":{"at":"\\ud83d\\ude00"},"actions":[]}' % r["id"])
    else:
        result(r["id"], "пицца")
""")
    res = {}
    for ev in ("btn:cesu", "btn:esc", "btn:pair", "btn:ru"):
        eng = Engine(fake, pizza, timeout=2.0)
        try:
            res[ev] = attempt(lambda: eng.step("u", {"at": "a"}, ev)["snapshot"]["at"])
        finally:
            eng.close()
    report(res == {"btn:cesu": "ошибка", "btn:esc": "ошибка", "btn:pair": "\U0001F600", "btn:ru": "пицца"},
           f"R18 суррогат байтами и экранированный — ошибка, пара и кириллица приняты: {res!r}")


def r19_codes():
    """Протокол §1: EngineError.code и retryable; ответ-ошибка неверной формы — нарушение."""
    fake = pyfake("""for r in requests():
    ev = r.get("event")
    if ev == "btn:new":
        out({"id": r["id"], "error": "снимок отвергнут", "code": "bad_snapshot"})
    elif ev == "btn:old":
        out({"id": r["id"], "error": "снимок отвергнут"})
    elif ev == "btn:badcode":
        out({"id": r["id"], "error": "x", "code": 5})
    elif ev == "btn:extra":
        out({"id": r["id"], "error": "x", "extra": 1})
    elif ev == "btn:slow":
        time.sleep(1.0); result(r["id"])
    else:
        result(r["id"])
""")
    eng = Engine(fake, pizza, timeout=0.5)

    def info(ev):
        try:
            eng.step("u", {"at": "a"}, ev)
            return "ответ"
        except EngineError as e:
            return (getattr(e, "code", "нет поля"), getattr(e, "retryable", "нет поля"))

    try:
        pid0 = eng._procs[0]._p.pid
        new, old, slow = info("btn:new"), info("btn:old"), info("btn:slow")
        same = eng._procs[0].alive and eng._procs[0]._p.pid == pid0
        time.sleep(0.6)
        badcode = info("btn:badcode")
        dead1 = not eng._procs[0].alive
        extra = info("btn:extra")
        dead2 = not eng._procs[0].alive
    finally:
        eng.close()
    report(new == ("bad_snapshot", False) and old == (None, False) and slow == (None, True) and same,
           f"R19 ошибка движка с code {new}, без code {old}, таймаут {slow} (code, retryable); процесс тот же {same}")
    report(badcode == (None, True) and dead1 and extra == (None, True) and dead2,
           f"R19 ответ-ошибка неверной формы (code не строка, лишнее поле) — сбой процесса: {badcode} {dead1}, "
           f"{extra} {dead2}")
    eng = Engine(engine_bin, pizza, timeout=2.0)
    try:
        try:
            eng.step("u", {"at": "menu", "bogus": 1}, "btn:x")
            r = "ответ"
        except EngineError as e:
            r = (getattr(e, "code", "нет поля"), getattr(e, "retryable", "нет поля"))
        alive = eng._procs[0].alive
    finally:
        eng.close()
    report(r != "ответ" and r[1] is False and (r[0] is None or type(r[0]) is str) and alive,
           f"R19 настоящий движок, снимок с лишним полем: (code, retryable) = {r}, процесс жив {alive}")


def r20_seq():
    """start(key, seq): поле seq — только когда задано."""
    fake = pyfake("for r in requests():\n"
                  "    out({'id': r['id'], 'snapshot': {'at': 'seq' + str(r['seq']) if 'seq' in r else 'noseq'},"
                  " 'actions': []})\n")
    eng = Engine(fake, pizza, timeout=2.0)
    try:
        a = attempt(lambda: eng.start("u")["snapshot"]["at"])
        b = attempt(lambda: eng.start("u", seq=41)["snapshot"]["at"])
    finally:
        eng.close()
    report(a == "noseq" and b == "seq41", f"R20 start без seq — {a}, start(seq=41) — {b}")


for fn in (r14_doc, r15_line, r10_startup, r10_real, r10_options, r11_slow_request, r11_real, r11_hang,
           r12_id_null, r12_real, r12_control, r13_doc_error, r13_backoff, r16_close, r17_step, r18_utf8,
           r19_codes, r20_seq):
    try:
        fn()
    except Exception as e:  # noqa: BLE001 — исключение теста = провал этой проверки
        report(False, f"{fn.__name__} ({fn.__doc__.split(':')[0] if fn.__doc__ else ''}): исключение {e!r}"[:300])
    wait_children(0, 2.0)

shutil.rmtree(tmp, ignore_errors=True)
print("клиент устойчив" if ok else "НАРУШЕНИЕ УСТОЙЧИВОСТИ КЛИЕНТА")
sys.exit(0 if ok else 1)

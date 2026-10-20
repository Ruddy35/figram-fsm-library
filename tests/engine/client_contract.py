"""Контракт публичных методов клиента (V4-02 … V4-05).

    python3 client_contract.py ENGINE features.bot.json
"""

import math
import os
import stat
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "clients", "python"))
from figram_client import Engine, EngineError

engine_bin, doc = sys.argv[1], sys.argv[2]
ok = True


def report(good, text):
    global ok
    ok &= good
    print(f"{'ok ' if good else 'НЕТ'} {text}")


def raises(fn):
    try:
        fn()
        return False
    except EngineError:
        return True


# V4-02: step никогда не возвращает незавершённый need.
eng = Engine(engine_bin, doc, timeout=2)
snap = eng.start("x")["snapshot"]
report(raises(lambda: eng.step("x", snap, "btn:go", guards=lambda ns: {})), "условие не заполнено → EngineError")
report(raises(lambda: eng.step("x", snap, "btn:go", guards=lambda ns: {"is_vip": 1})), "значение не bool → EngineError")
report(raises(lambda: eng.step("x", snap, "btn:go", guards=lambda ns: ["is_vip"])), "не dict → EngineError")
report(raises(lambda: eng.step("x", snap, "btn:go")), "условия нужны, функции нет → EngineError")
r = eng.step("x", snap, "btn:go", guards=lambda ns: {n: True for n in ns})
report("snapshot" in r and r["snapshot"]["at"] == "vip", "полные условия → результат шага")

# V4-03: невалидные аргументы не трогают таблицу ожидающих и процесс.
p = eng._procs[0]
bad_args = 0
for i in range(1000):
    arg = object() if i % 2 else math.nan
    try:
        eng.step("x", {"at": "home", "x": arg}, "btn:go")
    except EngineError:
        bad_args += 1
report(bad_args == 1000 and len(p._pending) == 0 and p.alive,
       f"1000 несериализуемых запросов: ошибок {bad_args}, ожидающих {len(p._pending)}, процесс жив {p.alive}")
report("snapshot" in eng.start("x"), "после них процесс обслуживает запросы")
eng.close()


# Повторный need → ошибка; аварийный процесс собран без следующих запросов (V4-04).
def state(pid):
    try:
        with open(f"/proc/{pid}/stat") as f:
            return f.read().rsplit(")", 1)[1].split()[0]
    except (FileNotFoundError, ProcessLookupError):
        # ProcessLookupError (ESRCH): процесс собран между open() и read().
        return "собран"


with tempfile.TemporaryDirectory() as d:
    def fake(body):
        """Тестовый двойник (sh): отвечает на hello при запуске, затем body."""
        path = os.path.join(d, "fake.sh")
        with open(path, "w") as f:
            f.write("#!/bin/sh\nread l; echo '{\"id\":0,\"protocol\":1,\"states\":1}'\n" + body + "\n")
        os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)
        return path

    eng = Engine(fake('read l; echo \'{"id":1,"need":["a"]}\'; read l; echo \'{"id":2,"need":["a"]}\'; sleep 30'),
                 doc, timeout=2)
    report(raises(lambda: eng.step("x", {"at": "home"}, "btn:go", guards=lambda ns: {n: True for n in ns})),
           "повторный need → EngineError")
    eng.close()

    eng = Engine(fake('read l; echo "{oops"; sleep 30'), doc, timeout=1)
    pid = eng._procs[0]._p.pid
    raises(lambda: eng.start("x"))
    t0 = time.monotonic()
    while state(pid) != "собран" and time.monotonic() - t0 < 2:
        time.sleep(0.02)
    report(state(pid) == "собран", f"аварийный процесс собран за {time.monotonic() - t0:.2f} с без новых запросов")
    t0 = time.monotonic()
    eng.close()
    report(time.monotonic() - t0 < 2, "close после аварии не зависает")

print("контракт клиента выполнен" if ok else "НАРУШЕНИЕ КОНТРАКТА КЛИЕНТА")
sys.exit(0 if ok else 1)

"""Клиент не зависает ни при каком поведении движка (N01).

Подставные «движки» нарушают протокол разными способами; каждый запрос из
многих потоков должен завершиться контролируемой ошибкой за ограниченное
время, а следующий запрос — уйти в новый, исправный процесс.

    python3 client_faults.py ENGINE bot.json
"""

import os
import stat
import sys
import tempfile
import threading
import time

from figram_client import Engine, EngineError

FAKES = {
    "невалидный JSON": 'read l; echo "{oops"; sleep 30',
    "неизвестный id": 'read l; echo \'{"id": 999, "snapshot": {}}\'; sleep 30',
    "ответ без id": 'read l; echo \'{"snapshot": {}}\'; sleep 30',
    "id = true": 'read l; echo \'{"id": true, "snapshot": {"at": "a"}, "actions": []}\'; sleep 30',
    "пустой результат": 'read l; echo \'{"id": 1}\'; sleep 30',
    "result + error": 'read l; echo \'{"id": 1, "snapshot": {"at": "a"}, "actions": [], "error": "x"}\'; sleep 30',
    "seq = true": 'read l; echo \'{"id": 1, "snapshot": {"at": "a", "seq": true}, "actions": []}\'; sleep 30',
    "неизвестное действие": 'read l; echo \'{"id": 1, "snapshot": {"at": "a"}, "actions": [{"jump": "x"}]}\'; sleep 30',
    "нет ответа": 'while read l; do :; done',
    "обрыв вывода": 'read l; exit 0',
    "падение": 'read l; kill -SEGV $$',
    "не читает вход": 'sleep 30',
    "мусор и не читает вход": 'echo "{oops"; sleep 30',
}
BIG = {"at": "home", "resume": {f"s{i}": "m" for i in range(7000)}}

real_engine, doc = sys.argv[1], sys.argv[2]
ok = True
with tempfile.TemporaryDirectory() as d:
    for name, body in FAKES.items():
        path = os.path.join(d, "fake.sh")
        with open(path, "w") as f:
            f.write(f"#!/bin/sh\n{body}\n")
        os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)
        engine = Engine(path, doc, processes=1, timeout=1.0)
        results = []

        def worker(i):
            try:
                if i % 2:
                    engine.step("u", BIG, "btn:x")   # большой запрос (~110 КБ)
                else:
                    engine.start("u")
                results.append("ответ")
            except EngineError:
                results.append("ошибка")

        t0 = time.monotonic()
        threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(10)
        hung = sum(t.is_alive() for t in threads)
        took = time.monotonic() - t0
        t1 = time.monotonic()
        engine.close()
        close_took = time.monotonic() - t1
        good = hung == 0 and results.count("ошибка") == 8 and took < 3.0 and close_took < 3.0
        ok &= good
        print(f"{'ok ' if good else 'НЕТ'} {name}: ошибок {results.count('ошибка')} из 8, "
              f"зависло {hung}, {took:.1f} с, close {close_took:.1f} с")

    # Много одновременных запросов к одному процессу, который не читает вход:
    # вместе ~300 КБ — больше буфера канала (64 КБ), писатель блокируется.
    # Каждый вызов обязан завершиться ошибкой в пределах одного таймаута.
    path = os.path.join(d, "fake.sh")
    with open(path, "w") as f:
        f.write("#!/bin/sh\nsleep 30\n")
    os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)
    engine = Engine(path, doc, processes=1, timeout=1.0)
    snap = {"at": "home", "resume": {f"s{i}": "m" for i in range(60)}}
    errors = []
    durations = []

    def small():
        t = time.monotonic()
        try:
            engine.step("u", snap, "btn:x")
        except EngineError:
            errors.append(1)
        durations.append(time.monotonic() - t)

    t0 = time.monotonic()
    threads = [threading.Thread(target=small) for _ in range(300)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    engine.close()
    took = time.monotonic() - t0
    hung = sum(t.is_alive() for t in threads)
    good = len(errors) == 300 and hung == 0 and max(durations) < 2.0
    ok &= good
    print(f"{'ok ' if good else 'НЕТ'} 300 одновременных запросов (~300 КБ) при заблокированном входе: "
          f"ошибок {len(errors)}, зависло {hung}, самый долгий вызов {max(durations):.2f} с, всего {took:.1f} с")

    # После сбоя пул заменяет процесс: следующий запрос к настоящему движку проходит.
    engine = Engine(real_engine, doc, processes=1, timeout=2.0)
    engine._procs[0]._fail("внедрённый сбой")
    try:
        engine.start("u")          # к неисправному процессу больше не уходит
    except EngineError:
        pass
    try:
        r = engine.start("u")
        print("ok  замена неисправного процесса:", r["snapshot"]["at"])
    except EngineError as e:
        ok = False
        print("НЕТ замена неисправного процесса:", e)
    engine.close()

print("клиент устойчив" if ok else "КЛИЕНТ ЗАВИСАЕТ ИЛИ НЕ ВОССТАНАВЛИВАЕТСЯ")
sys.exit(0 if ok else 1)

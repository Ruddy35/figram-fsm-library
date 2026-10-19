"""Самопроверка тестовой обвязки: каждая порча движка обязана сделать
соответствующий тест красным (V3-02 … V3-05, V3-08).

Подставные движки — посредники к настоящему движку, которые портят ответы,
отвечают не на всё, не дают мутаций или зависают.

    python3 harness_selftest.py ENGINE pizza.bot.json traces.jsonl FEATURES_TRACES.jsonl
"""

import os
import re
import stat
import subprocess
import sys
import tempfile
import time

engine, doc, traces, ftraces = (os.path.abspath(a) for a in sys.argv[1:5])
here = os.path.dirname(os.path.abspath(__file__))
features = os.path.join(os.path.dirname(doc), "features.bot.json")
schema = os.path.join(os.path.dirname(os.path.dirname(doc)), "snapshot.schema.json")

PROXY = """#!/usr/bin/env python3
import json, subprocess, sys
p = subprocess.Popen([{engine!r}] + sys.argv[1:], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)
n = 0
for line in sys.stdin:
    p.stdin.write(line); p.stdin.flush()
    r = json.loads(p.stdout.readline())
    n += 1
    out = []
    {code}
    for o in out:
        print(o, flush=True)
"""

# Каждая порча: код, который превращает ответ r в список строк вывода `out`.
EMIT = 'out.append("{oops" if r is None else json.dumps(r))'
CORRUPT = {
    "снимок": f'if n % 7 == 3 and "snapshot" in r: r["snapshot"] = {{"at": "nowhere"}}\n    {EMIT}',
    "id": f'if n % 7 == 3: r["id"] = -999\n    {EMIT}',
    "id сдвинут": f'if n % 7 == 3: r["id"] += 1\n    {EMIT}',
    "не JSON": f'if n % 7 == 3: r = None\n    {EMIT}',
    "seq = true": f'if "snapshot" in r and n % 3 == 1: r["snapshot"]["seq"] = True\n    {EMIT}',
    "жетон = true": f'[a.update(token=True) for a in r.get("actions", []) if "start_timer" in a]\n    {EMIT}',
    "need + error": f'if "need" in r: r["error"] = "contradictory"\n    {EMIT}',
    "need + snapshot": f'if "need" in r: r["snapshot"] = {{"at": "a"}}\n    {EMIT}',
    "результат + error": f'if "snapshot" in r and n % 5 == 2: r["error"] = "x"\n    {EMIT}',
    "пропуск ответа": f'if n != 3: {EMIT}',
    "дубль ответа": f'{EMIT}\n    if n == 3: {EMIT}',
}
TOOLS = {
    "conformance": lambda fake: [fake, f"{doc}={traces}"],
    "loadtest": lambda fake: [fake, doc, traces, "8", "1"],
    "snapshot_fixtures": lambda fake: [fake, features, schema],
}
# Порчи, которые к инструменту не относятся: в snapshot_fixtures нет need и
# таймеров; loadtest через клиент ловит пропуск таймаутом (проверено в client_faults).
SKIP = {("loadtest", "пропуск ответа"), ("loadtest", "дубль ответа"),
        ("snapshot_fixtures", "need + error"), ("snapshot_fixtures", "need + snapshot"),
        ("snapshot_fixtures", "жетон = true")}

ok = True


LAST = {"out": ""}


def report(good, text):
    global ok
    ok &= good
    print(f"{'ok ' if good else 'НЕТ'} {text}", flush=True)
    if not good:   # подробности отказа: хвост вывода проверяемого инструмента
        for line in LAST["out"].strip().splitlines()[-8:]:
            print(f"      | {line}", flush=True)


def run_tool(tool, args, env=None, timeout=600):
    t0 = time.monotonic()
    r = subprocess.run([sys.executable, os.path.join(here, f"{tool}.py")] + args,
                       capture_output=True, timeout=timeout, env=env)
    LAST["out"] = (r.stdout + r.stderr).decode(errors="replace")
    return r.returncode, time.monotonic() - t0


# Только сообщения о НАРУШЕНИИ: строки успешного итога («расхождений 0», «ответов 24») не подходят.
DETECTED = re.compile(r"расхождений [1-9]|расходятся|РАСХОЖДЕНИЕ|нарушает протокол|ответов \d+ из \d+|"
                      r"ответ \d+:|ошибка потока|выполнено шагов \d+ из|граница seq: НЕТ|шаг \d+:|: start$")


def caught():
    """Порча поймана, только если инструмент сообщил о нарушении или расхождении
    и не упал с трассировкой по посторонней причине."""
    out = LAST["out"]
    return "Traceback" not in out and bool(DETECTED.search(out))


def alive_after(pids, limit=5.0):
    """Живые (не зомби) процессы из списка; ждём до `limit` секунд — в медленных
    песочницах доставка SIGKILL и смена состояния занимают время."""
    deadline = time.monotonic() + limit
    while True:
        alive = []
        for pid in pids:
            try:
                state = open(f"/proc/{pid}/stat").read().rsplit(")", 1)[1].split()[0]
                if state != "Z":
                    alive.append(pid)
            except (FileNotFoundError, ProcessLookupError):
                pass
        if not alive or time.monotonic() > deadline:
            return alive
        time.sleep(0.05)


with tempfile.TemporaryDirectory() as d:
    def write(name, text):
        path = os.path.join(d, name)
        with open(path, "w") as f:
            f.write(text)
        os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)
        return path

    for name, code in CORRUPT.items():
        fake = write("fake.py", PROXY.format(engine=engine, code=code))
        for tool, args in TOOLS.items():
            if (tool, name) in SKIP:
                continue
            rc, took = run_tool(tool, args(fake))
            report(rc != 0 and caught(),
                   f"{tool}, порча «{name}»: код выхода {rc}, распознана {caught()} ({took:.1f} с)")

    # Ноль мутаций — провал, а не 100%.
    fake = write("nomut.sh", f'#!/bin/sh\nif [ "$1" = "--mutants" ]; then exit 0; fi\nexec {engine} "$@"\n')
    rc, _ = run_tool("mutation", [fake, f"{doc}={traces}"])
    report(rc != 0 and "меньше минимума" in LAST["out"] and "Traceback" not in LAST["out"],
           f"mutation, движок без мутаций: код выхода {rc}")

    # Зависший движок (без него проверка висела бы ~1000 с): проверка обязана
    # завершиться ошибкой ИМЕННО по сроку, намного раньше, без живых процессов.
    # Порог времени щедрый: смысл в «не висит», а не в скорости среды.
    pidfile = os.path.join(d, "pid")
    fake = write("hang.sh", f"#!/bin/sh\necho $$ > {pidfile}\nsleep 1000 &\necho $! >> {pidfile}\nwait\n")
    env = dict(os.environ, FIGRAM_TEST_TIMEOUT="2")
    for tool, args in [("conformance", [fake, f"{doc}={traces}"]),
                       ("snapshot_fixtures", [fake, features, schema])]:
        rc, took = run_tool(tool, args, env=env, timeout=300)
        by_deadline = "не завершился за" in LAST["out"]
        alive = alive_after(open(pidfile).read().split())
        report(rc != 0 and by_deadline and took < 120 and not alive,
               f"{tool}, зависший движок: код {rc}, прерван по сроку {by_deadline}, {took:.1f} с, "
               f"живых процессов {len(alive)}")

    # Положительный контроль: настоящий движок проходит все проверки.
    for tool, args in TOOLS.items():
        rc, took = run_tool(tool, args(engine))
        report(rc == 0, f"{tool}, настоящий движок: код выхода {rc} ({took:.1f} с)")
    golden = os.path.join(os.path.dirname(doc), "golden.jsonl")
    rc, took = run_tool("mutation", [engine, "--golden", golden, f"{doc}={traces}",
                                     f"{features}={ftraces}"], timeout=1800)
    report(rc == 0, f"mutation, настоящий движок на полном корпусе: код выхода {rc} ({took:.1f} с)")

print("обвязка ловит порчу" if ok else "ОБВЯЗКА ПРОПУСКАЕТ ПОРЧУ (см. строки «НЕТ» выше)")
sys.exit(0 if ok else 1)

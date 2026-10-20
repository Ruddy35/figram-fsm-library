"""Матрица сбоев движка, которых нет в client_faults.py. Подставные движки —
test doubles (sh/python), кроме случаев с пометкой REAL (настоящий движок).
Для каждого случая: исход 8 параллельных вызовов, время до ошибки при
timeout=3 с, потомки/зомби после.

    python3 r09_fault_matrix.py
"""
import os
import signal
import tempfile
import threading
import time

from _common import ENGINE, PIZZA, children, fake, label
from figram_client import Engine, EngineError

label()
TIMEOUT = 3.0
RESP = '{"id":%s,"snapshot":{"at":"a"},"actions":[]}'
ECHO = ('while read l; do id=$(echo "$l" | sed \'s/^{"id":\\([0-9]*\\).*/\\1/\'); '
        'echo "{\\"id\\":$id,\\"snapshot\\":{\\"at\\":\\"a\\"},\\"actions\\":[]}"; done')
tmp = tempfile.mkdtemp()
CASES = [
    # имя, тело, python?, ожидание
    ("полная строка без \\n, затем EOF", "read l; printf '%s'; exit 0" % (RESP % 1), False, "1 ответ, остальные ошибка"),
    ("обрезанная строка без \\n, затем EOF", "read l; printf '{\"id\":1,\"snap'; exit 0", False, "ошибка сразу"),
    ("невалидный UTF-8 (\\377)", "read l; printf '{\"id\":1,\"snapshot\":{\"at\":\"\\377\"},\"actions\":[]}\\n'; sleep 30", False, "ошибка сразу"),
    ("UTF-8-кодированный суррогат (ED A0 80)", "read l; printf '{\"id\":1,\"snapshot\":{\"at\":\"\\355\\240\\200\"},\"actions\":[]}\\n'; sleep 30", False, "ошибка сразу"),
    ("stdout закрыт, процесс жив", "exec 1>&-; while read l; do :; done", False, "ошибка сразу"),
    ("поток в stderr + верные ответы", "(yes flood >&2 &) ; " + ECHO, False, "все ответы"),
    ("ответ после таймаута (поздний)", "read l; sleep %s; echo '%s'; " % (TIMEOUT + 1, RESP % 1) + ECHO, False, "ошибка по таймауту"),
    ("двойной ответ на один id", "read l; echo '%s'; echo '%s'; sleep 30" % (RESP % 1, RESP % 1), False, "≤1 ответ, остальные ошибка"),
    ("ответы не по порядку id", None, True, "все ответы"),
]
REORDER = '''
import sys, json
buf = []
for line in sys.stdin:
    buf.append(json.loads(line)["id"])
    if len(buf) == 8:
        for i in reversed(buf):
            print(json.dumps({"id": i, "snapshot": {"at": "a"}, "actions": []}), flush=True)
        buf = []
'''


def run_case(name, binary, expect, prep=None):
    eng = Engine(binary, PIZZA, processes=1, timeout=TIMEOUT)
    if prep:
        prep(eng)
    res, times = [], []
    lk = threading.Lock()

    def worker():
        t = time.monotonic()
        try:
            eng.start("u")
            r = "ответ"
        except EngineError as e:
            r = "ошибка"
        with lk:
            res.append(r)
            times.append(time.monotonic() - t)

    ts = [threading.Thread(target=worker) for _ in range(8)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(15)
    pid = eng._procs[0]._p.pid
    eng.close()
    time.sleep(0.2)
    ch = children()
    print(f"{name:42} | ответов {res.count('ответ')}, ошибок {res.count('ошибка')}, зависло {sum(t.is_alive() for t in ts)}"
          f" | макс. время {max(times):.2f} с | потомков {len(ch)} зомби {sum(s == 'Z' for _, s in ch)} | ожидалось: {expect}", flush=True)


for name, body, py, expect in CASES:
    binary = fake(tmp, f"f{len(name)}.{'py' if py else 'sh'}", REORDER if py else body, python=py)
    run_case(name, binary, expect)


def stop_it(eng):
    os.kill(eng._procs[0]._p.pid, signal.SIGSTOP)


run_case("REAL: движок остановлен SIGSTOP", ENGINE, "ошибка по таймауту, процесс собран", stop_it)

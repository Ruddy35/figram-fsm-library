"""PC-12: _fail() вызывается несколько раз на один сбой (читатель: EOF,
писатель: EPIPE, вызывающий: таймаут), и КАЖДЫЙ раз делает
os.killpg(pid, SIGKILL) — в том числе после того, как фоновый wait() уже
собрал процесс и pid освобождён. Если pid к этому моменту переиспользован
лидером другой группы процессов (например, новым процессом движка из того
же пула, start_new_session=True), SIGKILL получит чужая группа.

Считает вызовы killpg по уже собранному pid (нет /proc/<pid>) за CYCLES
циклов «падение → замена» с подставным движком (test double), 8 потоков.

    python3 r12_killpg_after_reap.py [CYCLES]     # exit 1 = есть killpg по собранному pid
"""
import os
import sys
import tempfile
import threading

from _common import fake, label
import figram_client
from figram_client import Engine, EngineError

cycles = int(sys.argv[1]) if len(sys.argv) > 1 else 500
label()
calls = {"all": 0, "reaped": 0}
pids = set()
lk = threading.Lock()
real_killpg = os.killpg


def spy(pid, sig):
    with lk:
        calls["all"] += 1
        pids.add(pid)
        if not os.path.exists(f"/proc/{pid}"):
            calls["reaped"] += 1
    return real_killpg(pid, sig)


figram_client.os.killpg = spy
tmp = tempfile.mkdtemp()
binary = fake(tmp, "segv.sh", 'read l; kill -SEGV $$')
eng = Engine(binary, "doc.json", processes=1, timeout=2)
for _ in range(cycles):

    def one():
        try:
            eng.start("u")
        except EngineError:
            pass

    ts = [threading.Thread(target=one) for _ in range(8)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
eng.close()
print(f"{cycles} сбоев: вызовов killpg {calls['all']} по {len(pids)} разным pid, из них по уже собранному pid {calls['reaped']}; "
      f"kernel.pid_max={open('/proc/sys/kernel/pid_max').read().strip()}")
raise SystemExit(1 if calls["reaped"] else 0)

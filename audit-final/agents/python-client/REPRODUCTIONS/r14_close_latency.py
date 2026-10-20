"""PC-15: Engine.close() закрывает процессы по очереди, каждый ждёт до timeout:
пул из N зависших процессов закрывается за ≈ N × timeout (docstring
_Process.close обещает «не дольше timeout» на процесс; про пул ничего).
Подставной движок (test double): не читает вход и не выходит по EOF.

    python3 r14_close_latency.py [N] [TIMEOUT]
"""
import sys
import tempfile
import time

from _common import fake, label
from figram_client import Engine

n = int(sys.argv[1]) if len(sys.argv) > 1 else 4
timeout = float(sys.argv[2]) if len(sys.argv) > 2 else 1.0
label()
binary = fake(tempfile.mkdtemp(), "hung.sh", "trap '' TERM; sleep 30")
eng = Engine(binary, "doc.json", processes=n, timeout=timeout)
t0 = time.monotonic()
eng.close()
print(f"close() пула из {n} зависших процессов при timeout={timeout}: {time.monotonic() - t0:.2f} с")

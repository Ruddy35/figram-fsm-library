"""PC-07: движок (test double) пишет бесконечную строку без перевода строки.

Читатель клиента (`for raw in stdout`) не ограничивает длину строки: память
растёт со скоростью канала, пока вызов не истечёт по таймауту и процесс не
будет убит. Печатает пик RSS клиента (VmHWM) для нескольких значений timeout.
Запускать под prlimit, чтобы не уронить машину.

    prlimit --as=6000000000 -- python3 r07_endless_line.py [TIMEOUT ...]
"""
import sys
import tempfile
import time

from _common import fake, hwm_mb, label, rss_mb
from figram_client import Engine, EngineError

label()
tmp = tempfile.mkdtemp()
binary = fake(tmp, "endless.sh", 'read l; exec cat /dev/zero')
for timeout in [float(x) for x in sys.argv[1:]] or [0.5, 1.0, 2.0]:
    eng = Engine(binary, "doc.json", timeout=timeout)
    base = rss_mb()
    t0 = time.monotonic()
    try:
        eng.start("u")
        res = "ответ"
    except EngineError as e:
        res = f"EngineError: {str(e)[:80]}"
    except MemoryError:
        res = "MemoryError"
    took = time.monotonic() - t0
    eng.close()
    print(f"timeout={timeout}: {res}; {took:.1f} с; пик RSS клиента {hwm_mb():.0f} МБ (до вызова {base:.0f} МБ)", flush=True)

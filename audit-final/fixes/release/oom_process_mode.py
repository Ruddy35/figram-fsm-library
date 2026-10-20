"""Нехватка памяти в процессном режиме: движок под ulimit -v, хост Python без лимита."""
import os, sys, time
sys.path.insert(0, sys.argv[1])
from figram_client import Engine, EngineError
engine, doc, limit_mb, big_mb = sys.argv[2], sys.argv[3], int(sys.argv[4]), int(sys.argv[5])
wrap = os.path.join(os.path.dirname(os.path.abspath(__file__)), "engine_limited.sh")
with open(wrap, "w") as f:
    f.write(f"#!/bin/sh\nulimit -v {limit_mb * 1024}\nexec {engine} --max-request 0 \"$@\"\n")
os.chmod(wrap, 0o755)
eng = Engine(wrap, doc, timeout=120, restart_backoff=(0, 0))
pid0 = eng._procs[0]._p.pid
snap = eng.start("u")["snapshot"]
print(f"1. до сбоя: start ok, pid движка {pid0}, pid хоста {os.getpid()}")
t = time.monotonic()
try:
    r = eng.step("u", snap, "x" * (big_mb << 20))
    print(f"2. запрос {big_mb} МБ: ответ без ошибки ({str(r)[:80]}) за {time.monotonic()-t:.1f} с")
except EngineError as e:
    print(f"2. запрос {big_mb} МБ: EngineError за {time.monotonic()-t:.1f} с, retryable={e.retryable}, code={e.code}")
    print("   текст: " + str(e).replace("\n", " | ")[:400])
r = eng.start("u2")
pid1 = eng._procs[0]._p.pid
print(f"3. после сбоя: start ok ({r['snapshot']}), pid движка {pid1} (новый процесс: {pid1 != pid0}), хост жив: pid {os.getpid()}")
eng.close()

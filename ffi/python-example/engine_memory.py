"""Память figram-engine (P-09): запуск под RLIMIT_AS (`ulimit -v`) и резерв адресов после старта.

    python3 engine_memory.py ENGINE DOCUMENT

Рантайм Lean по умолчанию резервирует по 1 ГиБ под стек каждого своего потока и 1 ГиБ под арену
mimalloc (≈3,2 ГБ адресов при ≈10 МБ занятой памяти), и под `ulimit -v` 3 ГБ движок не запускался.
Теперь стеки системного размера, арена заранее не резервируется (ffi/figram_rt.c). Код 0 — всё в норме.
"""

import resource
import subprocess
import sys
import time

ENGINE, DOC = sys.argv[1], sys.argv[2]
MAX_VMSIZE_KB = 512 * 1024      # резерв адресов после старта
MAX_RSS_KB = 64 * 1024          # занятая память после старта и 2000 запросов
bad = 0


def report(ok, text):
    global bad
    bad += 0 if ok else 1
    print(("ok  " if ok else "НЕТ ") + text, flush=True)


REQS = b'{"id":1,"op":"hello"}\n{"id":2,"op":"start"}\n'
for mb in (64, 256):
    def limit(mb=mb):
        resource.setrlimit(resource.RLIMIT_AS, (mb << 20, resource.RLIM_INFINITY))
    p = subprocess.run([ENGINE, DOC], input=REQS, capture_output=True, timeout=30, preexec_fn=limit)
    out = p.stdout.decode(errors="replace").splitlines()
    good = p.returncode == 0 and len(out) == 2 and '"protocol":1' in out[0] and '"snapshot"' in out[1]
    report(good, f"ulimit -v {mb} МБ: код {p.returncode}, ответов {len(out)}"
           + ("" if good else f"; stderr: {p.stderr.decode(errors='replace')[:200]!r}"))

p = subprocess.Popen([ENGINE, DOC], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
p.stdin.write(b'{"id":1,"op":"hello"}\n' + b"".join(b'{"id":%d,"op":"start"}\n' % i for i in range(2, 2002)))
p.stdin.flush()
for _ in range(2001):
    p.stdout.readline()
time.sleep(0.1)
st = {l.split(":")[0]: int(l.split()[1]) for l in open(f"/proc/{p.pid}/status") if l.startswith(("VmSize", "VmRSS"))}
p.stdin.close()
p.wait(timeout=30)
report(st["VmSize"] <= MAX_VMSIZE_KB, f"резерв адресов после старта {st['VmSize'] >> 10} МБ (предел {MAX_VMSIZE_KB >> 10})")
report(st["VmRSS"] <= MAX_RSS_KB, f"занятая память после 2000 запросов {st['VmRSS'] >> 10} МБ (предел {MAX_RSS_KB >> 10})")
print(f"память движка: резерв адресов {st['VmSize'] >> 10} МБ, занято {st['VmRSS'] >> 10} МБ"
      + ("" if bad == 0 else f"; провалено {bad}"))
sys.exit(1 if bad else 0)

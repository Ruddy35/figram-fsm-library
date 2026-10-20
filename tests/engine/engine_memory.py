"""Память figram-engine (P-09): запуск под RLIMIT_AS (`ulimit -v`) и резерв адресов после старта.

    python3 engine_memory.py ENGINE DOCUMENT

Рантайм Lean по умолчанию резервирует по 1 ГиБ под стек каждого своего потока и 1 ГиБ под арену
mimalloc (≈3,2 ГБ адресов при ≈10 МБ занятой памяти), и под `ulimit -v` 3 ГБ движок не запускался.
Теперь арена заранее не резервируется (ffi/figram_rt.c), main работает на основном потоке процесса
(ffi/figram_exe.c), а потока цикла событий libuv с потоком ядра io_uring нет (ffi/figram_init.cpp, P-22):
процесс однопоточный. Обработчик переполнения стека рантайма у движка сохранён: глубокий документ под
маленьким `ulimit -s` завершает процесс сообщением «Stack overflow detected», а не немым SIGSEGV.
Код 0 — всё в норме.
"""

import os
import resource
import subprocess
import sys
import tempfile
import time

ENGINE, DOC = sys.argv[1], sys.argv[2]
MAX_VMSIZE_KB = 64 * 1024       # резерв адресов после старта
MAX_RSS_KB = 16 * 1024          # занятая память после старта и 2000 запросов
MAX_THREADS = 1                 # только основной поток: служебных потоков рантайм не запускает
bad = 0


def report(ok, text):
    global bad
    bad += 0 if ok else 1
    print(("ok  " if ok else "НЕТ ") + text, flush=True)


REQS = b'{"id":1,"op":"hello"}\n{"id":2,"op":"start"}\n'
for mb in (32, 64, 256):
    def limit(mb=mb):
        resource.setrlimit(resource.RLIMIT_AS, (mb << 20, resource.RLIM_INFINITY))
    p = subprocess.run([ENGINE, DOC], input=REQS, capture_output=True, timeout=30, preexec_fn=limit)
    out = p.stdout.decode(errors="replace").splitlines()
    good = p.returncode == 0 and len(out) == 2 and '"protocol":1' in out[0] and '"snapshot"' in out[1]
    report(good, f"ulimit -v {mb} МБ: код {p.returncode}, ответов {len(out)}"
           + ("" if good else f"; stderr: {p.stderr.decode(errors='replace')[:200]!r}"))

# Переполнение стека: 250 вложенных объектов при стеке 24 и 32 КиБ (при 8 МиБ — обычная ошибка документа).
# Окружение пустое: его строки лежат в начале стека основного потока и сдвигают границу. Без окружения
# обе сборки (официальная и из исходников) переполняются с сообщением при 16–48 КиБ, при 56–64 КиБ —
# не всегда, ниже 16 КиБ стека не хватает уже до запуска рантайма.
with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
    f.write('{"figram":1,"bot":' + '{"a":' * 250 + "1" + "}" * 250 + "}")
for kb in (24, 32):
    def small_stack(kb=kb):
        resource.setrlimit(resource.RLIMIT_STACK, (kb << 10, resource.RLIM_INFINITY))
    p = subprocess.run([ENGINE, f.name], input=REQS, capture_output=True, timeout=30, preexec_fn=small_stack, env={})
    err = p.stderr.decode(errors="replace")
    report(p.returncode == -6 and "Stack overflow detected" in err,
           f"ulimit -s {kb} КиБ, вложенность 250: код {p.returncode}, stderr {err.strip()[:80]!r}")
os.unlink(f.name)

p = subprocess.Popen([ENGINE, DOC], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
p.stdin.write(b'{"id":1,"op":"hello"}\n' + b"".join(b'{"id":%d,"op":"start"}\n' % i for i in range(2, 2002)))
p.stdin.flush()
for _ in range(2001):
    p.stdout.readline()
time.sleep(0.1)
st = {l.split(":")[0]: int(l.split()[1]) for l in open(f"/proc/{p.pid}/status") if l.startswith(("VmSize", "VmRSS", "Threads"))}
p.stdin.close()
p.wait(timeout=30)
report(st["VmSize"] <= MAX_VMSIZE_KB, f"резерв адресов после старта {st['VmSize'] >> 10} МБ (предел {MAX_VMSIZE_KB >> 10})")
report(st["VmRSS"] <= MAX_RSS_KB, f"занятая память после 2000 запросов {st['VmRSS'] >> 10} МБ (предел {MAX_RSS_KB >> 10})")
report(st["Threads"] <= MAX_THREADS, f"потоков {st['Threads']} (предел {MAX_THREADS})")
print(f"память движка: резерв адресов {st['VmSize'] >> 10} МБ, занято {st['VmRSS'] >> 10} МБ, потоков {st['Threads']}"
      + ("" if bad == 0 else f"; провалено {bad}"))
sys.exit(1 if bad else 0)

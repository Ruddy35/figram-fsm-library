"""Память модуля Figram в Python под нагрузкой: процесс Python (клиент figram_client) и процессы движка.

    python3 pymem_load.py CLIENT_DIR ENGINE DOC PROCESSES THREADS USERS SECONDS
    (ENGINE «-» — контроль: та же нагрузка без клиента и движка, ответы сразу)

Для каждого процесса: RSS (занято в ОЗУ), из него анонимной памяти (куча и стеки — собственная) и страниц файлов
(код, общий с другими процессами того же файла), Private_Dirty (только этого процесса), резерв адресов (VmSize),
«обязательство» — сумма размеров частных записываемых областей (столько ядро учитывает в Committed_AS), потоки.
Замер: до импорта клиента, после запуска пула, пик за время нагрузки (опрос каждые 0,2 с), после нагрузки.
"""
import os, random, sys, threading, time

CLIENT, ENGINE, DOC = sys.argv[1], sys.argv[2], sys.argv[3]
PROCS, THREADS, USERS, SECONDS = (int(x) for x in sys.argv[4:8])
KEYS = ("rss", "anon", "file", "priv", "vm", "commit", "thr")


def stat(pid):
    st = {}
    for line in open(f"/proc/{pid}/status"):
        k, _, v = line.partition(":")
        if k in ("VmRSS", "RssAnon", "RssFile", "VmSize", "Threads"):
            st[k] = int(v.split()[0])
    priv = 0
    for line in open(f"/proc/{pid}/smaps_rollup"):
        if line.startswith(("Private_Dirty:",)):
            priv = int(line.split()[1])
    commit, size, perm = 0, 0, ""
    for line in open(f"/proc/{pid}/smaps"):
        f = line.split()
        if len(f) >= 5 and "-" in f[0] and not f[0].endswith(":"):
            perm = f[1]
        elif f and f[0] == "Size:" and "w" in perm and perm.endswith("p"):
            commit += int(f[1])
    return dict(rss=st["VmRSS"], anon=st["RssAnon"], file=st["RssFile"], priv=priv, vm=st["VmSize"],
                commit=commit, thr=st["Threads"])


def line(label, s):
    print(f"{label:46} RSS {s['rss'] / 1024:6.1f} МБ (своя анонимная {s['anon'] / 1024:5.1f}, файлов {s['file'] / 1024:5.1f}), "
          f"Private_Dirty {s['priv'] / 1024:6.1f} МБ, резерв {s['vm'] / 1024:7.1f} МБ, обязательство {s['commit'] / 1024:6.1f} МБ, "
          f"потоков {s['thr']}", flush=True)


me = os.getpid()
base = stat(me)
line("Python: до импорта клиента", base)
sys.path.insert(0, CLIENT)
import figram_client  # noqa: E402

if ENGINE == "-":   # контроль: та же нагрузка без движка — ответы сразу, тем же видом
    class Fake:
        def start(self, key, seq=None):
            return {"id": 1, "snapshot": {"at": "menu", "resume": {"order": "menu"}, "seq": 1}, "actions": []}
        def step(self, key, snap, event, guards=None, token=None):
            return {"id": 2, "snapshot": dict(snap, seq=snap["seq"] + 1), "actions": [{"send": "menu"}]}
        def close(self):
            pass
    eng, pids = Fake(), []
else:
    eng = figram_client.Engine(ENGINE, DOC, processes=PROCS)
    pids = [p._p.pid for p in eng._procs]
line("Python: пул запущен", stat(me))
for i, pid in enumerate(pids):
    line(f"движок {i}: загружен, до запросов", stat(pid))

EVENTS = ["btn:order", "btn:pizza", "btn:size", "btn:add", "btn:cart", "btn:checkout", "btn:back", "btn:cancel"]
guards = lambda names: {n: True for n in names}
stop = time.monotonic() + SECONDS
done = [0] * THREADS
errors = [0]


def worker(t):
    rnd = random.Random(t)
    snaps = {}
    while time.monotonic() < stop:
        key = f"u{rnd.randrange(USERS)}"
        try:
            if key not in snaps:
                snaps[key] = eng.start(key)["snapshot"]
            else:
                r = eng.step(key, snaps[key], rnd.choice(EVENTS), guards)
                snaps[key] = r.get("snapshot", snaps[key])
            done[t] += 1
        except Exception:
            errors[0] += 1


peak = {pid: {k: 0 for k in KEYS} for pid in [me] + pids}
ths = [threading.Thread(target=worker, args=(t,)) for t in range(THREADS)]
t0 = time.monotonic()
for th in ths:
    th.start()
while any(th.is_alive() for th in ths):
    for pid in peak:
        s = stat(pid)
        for k in KEYS:
            peak[pid][k] = max(peak[pid][k], s[k])
    time.sleep(0.2)
for th in ths:
    th.join()
el = time.monotonic() - t0
print(f"нагрузка: {THREADS} потоков Python, {USERS} пользователей, {PROCS} процесс(а) движка, {el:.1f} с, "
      f"запросов {sum(done)} ({sum(done) / el:.0f}/с), ошибок {errors[0]}", flush=True)
line("Python: пик под нагрузкой", peak[me])
for i, pid in enumerate(pids):
    line(f"движок {i}: пик под нагрузкой", peak[pid])
line("Python: после нагрузки", stat(me))
for i, pid in enumerate(pids):
    line(f"движок {i}: после нагрузки", stat(pid))
tot = {k: sum(peak[p][k] for p in pids) for k in KEYS}
print(f"итого движки (пик): RSS {tot['rss'] / 1024:.1f} МБ, из них Private_Dirty {tot['priv'] / 1024:.1f} МБ; "
      f"Python сверх своего начала: RSS +{(peak[me]['rss'] - base['rss']) / 1024:.1f} МБ, "
      f"Private_Dirty +{(peak[me]['priv'] - base['priv']) / 1024:.1f} МБ", flush=True)
eng.close()
sys.exit(1 if errors[0] else 0)

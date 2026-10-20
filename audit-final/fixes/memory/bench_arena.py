"""Скорость и память движка при разных MIMALLOC_ARENA_RESERVE: N запросов start/step через stdin."""
import os, subprocess, sys, time, resource
engine, doc, n = sys.argv[1], sys.argv[2], int(sys.argv[3])
reqs = b"".join(b'{"id":%d,"op":"step","snapshot":{"at":"menu","seq":%d},"event":"btn:pizza"}\n' % (i, i) if i % 2 else
                b'{"id":%d,"op":"start"}\n' % i for i in range(1, n + 1))
for res in sys.argv[4:]:
    env = dict(os.environ)
    if res != "-": env["MIMALLOC_ARENA_RESERVE"] = res
    best = None
    for _ in range(3):
        t = time.perf_counter()
        p = subprocess.run([engine, doc], input=reqs, capture_output=True, env=env)
        dt = time.perf_counter() - t
        best = dt if best is None else min(best, dt)
    lines = p.stdout.count(b"\n")
    rss = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    print(f"arena_reserve={res:6} запросов {lines}, лучшее из 3: {best:.2f} с, {n/best/1000:.0f} тыс. запросов/с; пик RSS детей {rss} КБ")

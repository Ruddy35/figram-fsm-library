"""Скорость движка: N запросов start/step конвейером, лучшее из 3; варианты окружения через argv: «-» или K=V,K=V."""
import os, subprocess, sys, time
engine, doc, n = sys.argv[1], sys.argv[2], int(sys.argv[3])
reqs = b"".join(b'{"id":%d,"op":"step","snapshot":{"at":"menu","seq":%d},"event":"btn:pizza"}\n' % (i, i) if i % 2
                else b'{"id":%d,"op":"start"}\n' % i for i in range(1, n + 1))
for spec in sys.argv[4:]:
    env = dict(os.environ)
    if spec != "-": env.update(dict(kv.split("=", 1) for kv in spec.split(",")))
    best = min(_t for _t in (lambda: [(lambda t: (subprocess.run([engine, doc], input=reqs, capture_output=True, env=env), time.perf_counter() - t)[1])(time.perf_counter()) for _ in range(3)])())
    print(f"{spec:55} лучшее из 3: {best:.2f} с, {n / best / 1000:.0f} тыс. запросов/с")

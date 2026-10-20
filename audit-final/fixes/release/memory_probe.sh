#!/usr/bin/env bash
# Память figram-engine: запуск под ulimit -v, резерв против резидентной памяти.
cd /home/user/figram-fsm-library
for mb in 2500 3000 3200 3400 3500 4000; do
  ( ulimit -v $((mb*1024)); printf '{"id":1,"op":"hello"}\n' | timeout 10 bin/figram-engine examples/pizza.bot.json >/tmp/probe.out 2>&1; rc=$?; echo "ulimit -v $mb МБ: код $rc; $(head -c 160 /tmp/probe.out | tr '\n' ' ')" )
done
python3 -I - <<'PY'
import subprocess, time
p = subprocess.Popen(["bin/figram-engine", "examples/pizza.bot.json"], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
p.stdin.write(b'{"id":1,"op":"hello"}\n'); p.stdin.flush(); p.stdout.readline(); time.sleep(0.3)
print({l.split(":")[0]: l.split(":")[1].strip() for l in open(f"/proc/{p.pid}/status") if l.startswith(("VmSize", "VmRSS", "Threads"))})
maps, cur = [], None
for l in open(f"/proc/{p.pid}/smaps"):
    f = l.split()
    if "-" in f[0] and ":" not in f[0]:
        a, b = (int(x, 16) for x in f[0].split("-")); cur = [(b - a) >> 20, f[1], f[5] if len(f) > 5 else "(анонимная)", 0]; maps.append(cur)
    elif f[0] == "Rss:":
        cur[3] = int(f[1]) >> 10
maps.sort(key=lambda m: -m[0])
for m in maps[:5]: print(f"резерв {m[0]:5} МБ {m[1]} резидентно {m[3]} МБ {m[2]}")
print("прочие отображения, МБ:", sum(m[0] for m in maps[5:]))
p.stdin.close(); p.wait()
PY

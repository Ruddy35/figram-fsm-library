"""20000 запросов к figram-engine; запускается внутри memory-cgroup (cgroup_probe.sh)."""
import subprocess
p = subprocess.Popen(["bin/figram-engine", "examples/pizza.bot.json"], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
reqs = [b'{"id":0,"op":"hello"}\n'] + [b'{"id":%d,"op":"start"}\n' % i for i in range(1, 20000)]
out, _ = p.communicate(b"".join(reqs))
lines = out.splitlines()
errors = sum(1 for l in lines if b'"error"' in l)
print(f"ответов {len(lines)} из 20000, ошибок {errors}, код выхода движка {p.returncode}")
print("пример ответа:", lines[1][:100].decode())

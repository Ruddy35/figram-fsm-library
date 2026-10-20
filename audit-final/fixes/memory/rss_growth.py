"""RSS движка по ходу потока запросов: растёт ли память с числом запросов."""
import subprocess, sys, threading
engine, doc, total = sys.argv[1], sys.argv[2], int(sys.argv[3])
p = subprocess.Popen([engine, doc], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
def rss():
    for l in open(f"/proc/{p.pid}/status"):
        if l.startswith("VmRSS"): return int(l.split()[1])
done = 0
marks = {1000, 10000, 50000, 100000, 200000, 400000}
for i in range(1, total + 1):
    req = (b'{"id":%d,"op":"step","snapshot":{"at":"menu","seq":%d},"event":"btn:pizza"}\n' % (i, i) if i % 2
           else b'{"id":%d,"op":"start"}\n' % i)
    p.stdin.write(req); p.stdin.flush(); p.stdout.readline()
    if i in marks: print(f"после {i:6} запросов (по одному, с ответом): RSS {rss()} КБ", flush=True)
p.stdin.close(); p.wait()

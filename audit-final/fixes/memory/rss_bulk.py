"""RSS движка, когда весь поток запросов записан сразу (конвейер), а ответы читаются параллельно."""
import subprocess, sys, threading, time
engine, doc, n = sys.argv[1], sys.argv[2], int(sys.argv[3])
reqs = b"".join(b'{"id":%d,"op":"step","snapshot":{"at":"menu","seq":%d},"event":"btn:pizza"}\n' % (i, i) if i % 2
                else b'{"id":%d,"op":"start"}\n' % i for i in range(1, n + 1))
p = subprocess.Popen([engine, doc], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
got = [0]
def reader():
    for _ in p.stdout: got[0] += 1
t = threading.Thread(target=reader); t.start()
def writer():
    p.stdin.write(reqs); p.stdin.close()
w = threading.Thread(target=writer); w.start()
peak = 0
while p.poll() is None:
    try:
        for l in open(f"/proc/{p.pid}/status"):
            if l.startswith("VmRSS"): r = int(l.split()[1]); peak = max(peak, r)
    except FileNotFoundError: break
    time.sleep(0.05)
t.join(); w.join()
print(f"{n} запросов конвейером ({len(reqs)>>20} МБ на входе): ответов {got[0]}, пик RSS движка {peak} КБ")

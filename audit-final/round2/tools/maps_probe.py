import json, subprocess, sys, os
eng, bot, n, count = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4])
seq = 5_000_000
snap = {"at": "home", "seq": seq, "pending": [{"call": "award", "token": seq - i} for i in range(n)]}
req = {"id": 1, "op": "step", "snapshot": snap, "event": "call:award:ok", "token": seq}
line = (json.dumps(req, separators=(",", ":")) + "\n").encode()
p = subprocess.Popen([eng, bot], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
def stat():
    maps = open(f"/proc/{p.pid}/maps").read().splitlines()
    rss = [l for l in open(f"/proc/{p.pid}/status") if l.startswith("VmRSS")][0].split()[1]
    anon = 0
    for l in open(f"/proc/{p.pid}/smaps"):
        if l.startswith("Anonymous:"): anon += int(l.split()[1])
    return len(maps), int(rss), anon
for i in range(1, count + 1):
    p.stdin.write(line); p.stdin.flush(); p.stdout.readline()
    if i in (10, 100, 500, 1000, 2000, count):
        print(i, "maps=%d rss=%dKB anon=%dKB" % stat())
p.stdin.close(); p.wait()

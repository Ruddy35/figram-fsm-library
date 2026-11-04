import json, subprocess, sys
eng, bot, n, count = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4])
seq = 5_000_000
snap = {"at": "home", "seq": seq, "pending": [{"call": "award", "token": seq - i} for i in range(n)]}
req = {"id": 1, "op": "step", "snapshot": snap, "event": "call:award:ok", "token": seq}
line = (json.dumps(req, separators=(",", ":")) + "\n").encode()
p = subprocess.Popen([eng, bot], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
def snapmaps():
    out = []; cur = None
    for l in open(f"/proc/{p.pid}/smaps"):
        parts = l.split()
        if '-' in parts[0] and len(parts) >= 5 and ':' not in parts[0]:
            cur = [parts[0], parts[-1] if len(parts) >= 6 else '', 0, 0]; out.append(cur)
        elif parts[0] == 'Size:': cur[2] = int(parts[1])
        elif parts[0] == 'Rss:': cur[3] = int(parts[1])
    return out
for i in range(1, count + 1):
    p.stdin.write(line); p.stdin.flush(); p.stdout.readline()
    if i in (10, count):
        print("after", i)
        for m in snapmaps():
            if m[3] > 500 and not m[1].startswith('/'): print("  ", m)
p.stdin.close(); p.wait()

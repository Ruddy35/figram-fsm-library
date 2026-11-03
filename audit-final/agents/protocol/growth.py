"""`every` waits accumulate without bound: the engine returns a snapshot that it then rejects as
too_large under its own default request limit (1 MiB). usage: growth.py ENGINE features.bot.json"""
import json, subprocess, sys
eng = subprocess.Popen([sys.argv[1], sys.argv[2]], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
def req(r):
    line = json.dumps(r, separators=(",", ":")) + "\n"
    eng.stdin.write(line.encode()); eng.stdin.flush()
    return len(line) - 1, json.loads(eng.stdout.readline())
n = 35315
snap = {"at": "home", "pending": [{"call": "ping", "token": 10 + i} for i in range(n)], "seq": 10 ** 6}
for i in range(8):
    size, r = req({"id": i, "op": "step", "snapshot": snap, "event": "btn:ping"})
    if "snapshot" not in r:
        print(f"step {i}: request {size} bytes -> {r}")
        break
    snap = r["snapshot"]
    print(f"step {i}: request {size} bytes -> result, pending {len(snap['pending'])}")
eng.stdin.close(); eng.wait(10)

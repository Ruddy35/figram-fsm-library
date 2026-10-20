"""X1: время ответа настоящего движка на шаг со снимком из n записей resume.
    python3 big_snap_timing.py ENGINE DOC [n ...]
"""
import json, subprocess, sys, time
eng, doc = sys.argv[1], sys.argv[2]
sizes = [int(x) for x in sys.argv[3:]] or [1000, 10000, 50000, 100000]
for n in sizes:
    req = json.dumps({"id": 1, "op": "step", "snapshot": {"at": "menu", "resume": {f"s{i}": "m" for i in range(n)}}, "event": "btn:x"})
    t = time.monotonic()
    p = subprocess.run([eng, doc], input=(req + "\n").encode(), capture_output=True, timeout=300)
    print(n, len(req), "bytes", f"{time.monotonic()-t:.2f}s", p.stdout[:120])

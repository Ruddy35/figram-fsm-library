"""X1: время на длинное событие и на снимок с n таймерами.
    python3 big_event_timing.py ENGINE DOC
"""
import json, subprocess, sys, time
eng, doc = sys.argv[1], sys.argv[2]
for n in (4096, 100000, 1000000):
    req = json.dumps({"id": 1, "op": "step", "snapshot": {"at": "menu"}, "event": "btn:" + "x" * n})
    t = time.monotonic()
    p = subprocess.run([eng, doc], input=(req + "\n").encode(), capture_output=True, timeout=120)
    print("event", n, f"{time.monotonic()-t:.2f}s", p.stdout[:100])
for n in (1000, 5000, 10000, 20000):
    req = json.dumps({"id": 1, "op": "step", "snapshot": {"at": "menu", "timers": {f"t{i}": i for i in range(n)}}, "event": "btn:x"})
    t = time.monotonic()
    p = subprocess.run([eng, doc], input=(req + "\n").encode(), capture_output=True, timeout=120)
    print("timers", n, len(req), f"{time.monotonic()-t:.2f}s", p.stdout[:100])

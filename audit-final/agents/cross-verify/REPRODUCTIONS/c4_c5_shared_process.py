#!/usr/bin/env python3
"""C4 + C5 (cross-verify, independent): one bad request takes down the shared
engine process of figram_client.py, failing other users' requests.

C4: a slow request (snapshot with K `resume` entries; parse/decoding is super-linear)
    exceeds the client timeout -> client kills the process -> every pending request
    fails; the host retries the slow request (HOST.md §5) -> it kills again.
C5: a request the engine cannot parse (duplicate key produced by a Python dict
    {1: ..., "1": ...}) gets {"id": null, "error": ...} -> client kills the process.

Both: 1 engine process (Engine(processes=1), the client default), 32 "normal" users
stepping in a tight loop in threads (pipelined requests are in flight), then the bad request; we count normal requests that
raised EngineError.  CONTROL = same run without the bad request -> 0 failures.

Usage: c4_c5_shared_process.py ENGINE PIZZA_DOC {c4|c5|c5semantic|control} [K] [TIMEOUT] [RETRIES] [N_USERS] [PAUSE]
"""
import json
import os
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "ffi", "python-example"))
from figram_client import Engine, EngineError  # noqa: E402


def normal_users(eng, stop, stats, n_users=32, pause=0.0):
    def run(u):
        snap = eng.start(u)["snapshot"]
        while not stop.is_set():
            for ev in ("btn:order", "btn:cart", "btn:cancel"):
                try:
                    r = eng.step(u, snap, ev, lambda ns: {k: True for k in ns})
                    snap = r["snapshot"]
                    stats["ok"] += 1
                except EngineError as e:
                    stats["fail"] += 1
                    stats.setdefault("reasons", set()).add(str(e)[:90])
                    time.sleep(0.05)
                if pause:
                    time.sleep(pause)
    ts = [threading.Thread(target=run, args=(f"user{i}",), daemon=True) for i in range(n_users)]
    for t in ts:
        t.start()
    return ts


def main():
    engine_bin, doc, mode = sys.argv[1], sys.argv[2], sys.argv[3]
    K = int(sys.argv[4]) if len(sys.argv) > 4 else 60000
    timeout = float(sys.argv[5]) if len(sys.argv) > 5 else 10.0
    retries = int(sys.argv[6]) if len(sys.argv) > 6 else 2
    n_users = int(sys.argv[7]) if len(sys.argv) > 7 else 32
    pause = float(sys.argv[8]) if len(sys.argv) > 8 else 0.0
    print(f"engine={engine_bin} mode={mode} client timeout={timeout}s (default is 10.0)")
    if mode == "c4":
        snap = {"at": "cart", "resume": {f"s{i}": "catalog" for i in range(K)}}
        req = {"id": 1, "op": "step", "snapshot": snap, "event": "btn:cart"}
        t0 = time.monotonic()
        p = subprocess.run([engine_bin, doc], input=(json.dumps(req) + "\n").encode(),
                           capture_output=True, timeout=300)
        direct = time.monotonic() - t0
        print(f"direct engine time for the K={K} request (no client): {direct:.2f}s rc={p.returncode} "
              f"reply starts {p.stdout[:40]!r}")
    eng = Engine(engine_bin, doc, timeout=timeout)       # processes=1 (default)
    stats = {"ok": 0, "fail": 0}
    stop = threading.Event()
    ts = normal_users(eng, stop, stats, n_users, pause)
    print(f"normal users: {n_users}, pause between their steps {pause}s")
    time.sleep(1.0)
    base_ok = stats["ok"]
    print(f"warm-up: {base_ok} normal requests ok, {stats['fail']} failed")
    pid0 = eng._procs[0]._p.pid
    bad_results = []
    if mode in ("c4", "c5", "c5semantic"):
        for attempt in range(1 + retries):               # host retries per HOST.md §5
            t0 = time.monotonic()
            try:
                if mode == "c4":
                    eng.step("victim", snap, "btn:cart")
                elif mode == "c5":
                    eng.step("victim", {"at": "cart", "resume": {1: "catalog", "1": "cart"}}, "btn:cart")
                else:   # semantic error WITH id (unknown snapshot field): must not kill the process
                    eng.step("victim", {"at": "cart", "x": 1}, "btn:cart")
                bad_results.append(("ok", round(time.monotonic() - t0, 2)))
            except EngineError as e:
                bad_results.append((str(e)[:80], round(time.monotonic() - t0, 2)))
            time.sleep(0.3)
    else:
        time.sleep(3.0)
    time.sleep(1.0)
    stop.set()
    for t in ts:
        t.join(15)
    pid1 = eng._procs[0]._p.pid
    print(f"bad request attempts: {bad_results}")
    print(f"normal users: ok={stats['ok']} FAILED={stats['fail']}; engine pid {pid0} -> {pid1}")
    for r in sorted(stats.get("reasons", ())):
        print(f"  failure reason seen by a NORMAL user: {r}")
    eng.close()
    if mode == "control":
        ok = stats["fail"] == 0
    elif mode == "c5semantic":
        ok = stats["fail"] == 0 and pid0 == pid1
    else:
        ok = stats["fail"] > 0 and pid0 != pid1
    print(("REPRODUCED" if mode in ("c4", "c5") else "CONTROL GREEN") if ok else "NOT REPRODUCED / CONTROL RED")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()

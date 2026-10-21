#!/usr/bin/env python3
"""(f) Scaling benchmark of the JSON text layer (P-10): time and peak RSS of
`figram json` and `figram-engine` on ordinary large inputs.

Inputs (all well-formed, ≤ 20 MB): an object with 16k / 32k keys, numbers with
160k digits (integer and fraction), strings of 5 / 20 MB; for the engine the same
shapes inside a request (`guards` with 32k keys, a 160k-digit `id` that is
echoed, a 20 MB `event`).  Each run is one child process under `nice -n 10`
with RLIMIT_AS 6 GiB and a wall-clock timeout; RSS is the child's peak
(`wait4` ru_maxrss of a child started by a small measurer process; the floor of
the method — the same for a trivial child — is printed first).

    python3 f_parser_scaling.py --figram BIN --engine BIN [--check] [--json OUT]

With --check the run fails (exit 1) if any input exceeds its budget: time
≤ 1.5 s per input for keys/digits, ≤ 3 s for strings, and peak RSS ≤ 8 × input
size + 64 MiB.  The baseline parser (List Char, linear duplicate-key search,
digit-by-digit Nat accumulation) needs 4.5 s for 32k keys, ≈10 s for 160k digits
and ≈50 × the input in RSS (agents/formats-security, ISSUES_PRODUCT P-10).
"""

import argparse
import json
import os
import signal
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DOC = os.path.join(ROOT, "examples", "features.bot.json")
MB = 1_000_000


def digits(n):
    return ("1234567890" * (n // 10 + 1))[:n]


def obj_keys(n):
    return "{" + ",".join(f'"k{i}":{i}' for i in range(n)) + "}"


# Peak RSS of a child counts the memory of the process it was forked from (Linux
# records the pre-exec mm's high-water mark), so the child is started by a small
# fresh measurer process, not by this one (which holds the generated inputs).
MEASURER = r"""
import json, os, resource, sys, time
os.nice(10)
resource.setrlimit(resource.RLIMIT_AS, (6 << 30, 6 << 30))
inp, rep, cmd = sys.argv[1], sys.argv[2], sys.argv[3:]
fa = [(os.POSIX_SPAWN_OPEN, 0, inp, os.O_RDONLY, 0), (os.POSIX_SPAWN_OPEN, 2, os.devnull, os.O_WRONLY, 0)]
t0 = time.monotonic()
pid = os.posix_spawn(cmd[0], cmd, os.environ, file_actions=fa)
_, status, ru = os.wait4(pid, 0)
with open(rep, "w") as f:
    json.dump({"rc": os.waitstatus_to_exitcode(status), "sec": time.monotonic() - t0,
               "rss": ru.ru_maxrss * 1024}, f)
"""


def measure(cmd, data: bytes, timeout: float, tmp: str):
    """Run cmd with data on stdin; return (seconds, peak RSS bytes, rc, stdout)."""
    path = os.path.join(tmp, f"f-scaling-{os.getpid()}.in")
    rep = path + ".json"
    with open(path, "wb") as f:
        f.write(data)
    try:
        p = subprocess.Popen([sys.executable, "-I", "-c", MEASURER, path, rep] + cmd, stdout=subprocess.PIPE,
                             start_new_session=True)
        try:
            out, _ = p.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(p.pid, signal.SIGKILL)
            p.communicate()
            return timeout, 0, -9, b""
        with open(rep) as f:
            r = json.load(f)
    finally:
        for x in (path, rep):
            if os.path.exists(x):
                os.unlink(x)
    return r["sec"], r["rss"], r["rc"], out


def engine_args(engine):
    """`--max-request 0` (no limit) if the engine knows the option: the 20 MB request
    measures the parser, not the size limit."""
    r = subprocess.run([engine, "--max-request", "0", DOC], input=b'{"id":1,"op":"hello"}\n',
                       capture_output=True, timeout=60)
    return ["--max-request", "0"] if r.returncode == 0 else []


def cases(engine_extra):
    H = '{"at":"home"}'
    yield ("json: object 16k keys", "json", obj_keys(16000), "keys")
    yield ("json: object 32k keys", "json", obj_keys(32000), "keys")
    yield ("json: integer 160k digits", "json", "1" + digits(159999), "digits")
    yield ("json: fraction 160k digits", "json", "0." + digits(160000), "digits")
    yield ("json: string 5 MB", "json", '"' + "a" * (5 * MB) + '"', "string")
    yield ("json: string 20 MB", "json", '"' + "a" * (20 * MB) + '"', "string")
    g = ",".join(f'"g{i}":true' for i in range(32000))
    yield ("engine: guards 32k keys", "engine",
           '{"id":1,"op":"step","snapshot":' + H + ',"event":"btn:self","guards":{' + g + '}}', "keys")
    yield ("engine: id 160k digits (echo)", "engine", '{"id":1' + digits(159999) + ',"op":"hello"}', "digits")
    yield ("engine: event 20 MB", "engine",
           '{"id":1,"op":"step","snapshot":' + H + ',"event":"' + "e" * (20 * MB) + '"}', "string")


BUDGET_S = {"keys": 1.5, "digits": 1.5, "string": 3.0}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--figram", required=True)
    ap.add_argument("--engine", required=True)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--json", default=None)
    ap.add_argument("--timeout", type=float, default=120)
    ap.add_argument("--tmp", default=os.environ.get("TMPDIR", "/tmp"))
    a = ap.parse_args()
    extra = engine_args(a.engine)
    rows, over = [], 0
    # floor of the method: peak RSS of a trivial child started the same way
    _, floor, _, _ = measure(["/bin/true"], b"", a.timeout, a.tmp)
    print(f"measurement floor (/bin/true): RSS {floor / 2**20:.1f} MiB")
    for name, tool, text, kind in cases(extra):
        data = text.encode() + b"\n"
        cmd = [a.figram, "json"] if tool == "json" else [a.engine] + extra + [DOC]
        sec, rss, rc, out = measure(cmd, data, a.timeout, a.tmp)
        line = out.split(b"\n", 1)[0]
        ok_reply = line.startswith(b"OK ") if tool == "json" else line.startswith(b'{"id":')
        budget_rss = 8 * len(data) + (64 << 20)
        fine = rc == 0 and ok_reply and sec <= BUDGET_S[kind] and rss <= budget_rss
        over += not fine
        rows.append({"input": name, "bytes": len(data), "seconds": round(sec, 3), "rss_mb": round(rss / 2**20, 1),
                     "rss_per_input_byte": round(rss / len(data), 1), "rc": rc, "reply_ok": ok_reply,
                     "within_budget": fine})
        print(f"{'    ' if fine else 'OVER'} {name:32s} {len(data) / MB:8.2f} MB  {sec:7.2f} s  "
              f"RSS {rss / 2**20:8.1f} MiB ({rss / len(data):6.1f}×)  rc={rc} reply={line[:40]!r}", flush=True)
    if a.json:
        with open(a.json, "w") as f:
            json.dump(rows, f, indent=1, ensure_ascii=False)
    print(f"inputs={len(rows)} over-budget={over} engine-options={extra}")
    sys.exit(1 if a.check and over else 0)


if __name__ == "__main__":
    main()

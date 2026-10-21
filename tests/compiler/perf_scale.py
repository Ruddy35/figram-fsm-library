#!/usr/bin/env python3
"""P-06 / P-07: load time (check + compile) and step cost on large documents.

  python3 perf_scale.py --engine BIN --figram BIN [--only a,b] [--load-timeout S]
                        [--steps N] [--check] [--out results.jsonl]
                        [--max-load-s S] [--max-step-us U]

Scenarios are the series of the audit agents host-perf (gen_scale_doc.py = their
gen_doc.py) and core-compiler (perf.py).  For each document:
  * load:  CPU and wall time of `figram-engine DOC` answering one `hello` and exiting
           (parse + check + compile + wf/namesOK), measured with wait4 rusage;
  * check: (with --check) CPU of `figram check DOC`;
  * step:  CPU per `step` request (from /proc/PID/stat, pipelined batches) for
           each event of the scenario, from the `start` snapshot.
Optional thresholds turn the run into a regression test (exit 1 if exceeded).
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import perf  # noqa: E402  (core-compiler scenarios: flat, deep, many_hist)

TICK = os.sysconf("SC_CLK_TCK")


def gen(**kw):
    args = [sys.executable, os.path.join(HERE, "gen_scale_doc.py")]
    for k, v in kw.items():
        args += [f"--{k}", str(v)]
    return subprocess.run(args, capture_output=True, text=True, check=True).stdout


G = "btn:next"   # guarded event of gen_scale_doc messages (c1 … else)
SCEN = {
    # host-perf series
    "states1000": (lambda f: gen(states=1000, branches=2, resume=1, timers=0), ["btn:none", G, "cmd:start"]),
    "states3000": (lambda f: gen(states=3000, branches=2, resume=1, timers=0), ["btn:none", G, "cmd:start"]),
    "unique1000": (lambda f: gen(states=1000, branches=2, resume=1, timers=10, events="unique"),
                   ["btn:none", G, "btn:go0"]),
    "unique2000": (lambda f: gen(states=2000, branches=2, resume=1, timers=10, events="unique"),
                   ["btn:none", G, "btn:go0"]),
    "depth64": (lambda f: gen(states=300, depth=64, groups=10, branches=2, resume=1, timers=0),
                ["btn:none", G, "btn:up0"]),
    "resume100": (lambda f: gen(states=1000, groups=100, branches=2, resume=100, timers=0),
                  ["btn:none", G, "cmd:help"]),
    "global300": (lambda f: gen(states=1000, branches=2, resume=1, timers=0, **{"global": 300}),
                  ["btn:none", G, "cmd:g299"]),
    "global_big": (lambda f: gen(states=1000, branches=2, resume=1, timers=0, **{"global": 1000}),
                   ["btn:none", G, "cmd:g999"]),
    "random1000": (lambda f: subprocess.run([f, "random", "1", "1000"], capture_output=True, text=True,
                                            check=True).stdout, ["btn:none", "btn:a", "cmd:start"]),
    # core-compiler scenarios
    "flat_4000": (lambda f: json.dumps(perf.flat(4000)), ["btn:none", "btn:0"]),
    "hist_100x20": (lambda f: json.dumps(perf.many_hist(100, 20)), ["btn:none", "btn:go7"]),
    "hist_200x20": (lambda f: json.dumps(perf.many_hist(200, 20)), ["btn:none", "btn:go7"]),
    "deep_100_x_500leaves_hist": (lambda f: json.dumps(perf.deep(100, leaves=500, hist=True)),
                                  ["btn:none", "btn:n"]),
}
DEFAULT = ["states3000", "unique1000", "hist_100x20", "depth64", "global_big"]


def run_wait4(args, stdin_text, timeout):
    t0 = time.time()
    with tempfile.TemporaryFile("w+") as fin, tempfile.TemporaryFile("w+") as fout:
        fin.write(stdin_text)
        fin.flush()
        fin.seek(0)
        pid = os.fork()
        if pid == 0:
            os.dup2(fin.fileno(), 0)
            os.dup2(fout.fileno(), 1)
            devnull = os.open(os.devnull, os.O_WRONLY)
            os.dup2(devnull, 2)
            os.execv(args[0], args)
        deadline = t0 + timeout
        while True:
            wpid, status, ru = os.wait4(pid, os.WNOHANG)
            if wpid:
                break
            if time.time() > deadline:
                os.kill(pid, 9)
                os.wait4(pid, 0)
                return None, None, round(time.time() - t0, 2), ""
            time.sleep(0.02)
        fout.seek(0)
        out = fout.read()
    rc = os.waitstatus_to_exitcode(status)
    return rc, round(ru.ru_utime + ru.ru_stime, 3), round(time.time() - t0, 3), out


def proc_cpu(pid):
    with open(f"/proc/{pid}/stat") as f:
        parts = f.read().rsplit(")", 1)[1].split()
    return (int(parts[11]) + int(parts[12])) / TICK


def last_leaf(doc_text):
    """Last message in document pre-order: its rows are at the end of a flat row list."""
    out = []

    def walk(name, node):
        if "children" in node:
            for k, v in node["children"].items():
                walk(k, v)
        else:
            out.append(name)
    walk("", json.loads(doc_text)["bot"])
    return out[-1]


def step_cost(engine, doc, events, steps, timeout, last):
    p = subprocess.Popen([engine, doc], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         stderr=subprocess.DEVNULL, text=True, bufsize=1)

    def ask(obj):
        p.stdin.write(json.dumps(obj) + "\n")
        p.stdin.flush()
        return json.loads(p.stdout.readline())

    hello = ask({"id": 0, "op": "hello"})
    snap = ask({"id": 1, "op": "start"})["snapshot"]
    res = {"states": hello.get("states"), "last": last}
    for where, ev in [("start", e) for e in events] + [("last", e) for e in events]:
        sn = snap if where == "start" else {"at": last, "seq": 5}
        req = {"op": "step", "snapshot": sn, "event": ev}
        r = ask(dict(req, id=2))
        if "need" in r:
            req["guards"] = {g: False for g in r["need"]}
            r = ask(dict(req, id=2))
        kind = "error" if "error" in r else ("moved" if r.get("actions") else "ignored")
        line = json.dumps(req)
        c0, t0 = proc_cpu(p.pid), time.time()
        n = 0
        # at least `steps` requests and at least 0.5 s of engine CPU (tick resolution 10 ms)
        while (n < steps or proc_cpu(p.pid) - c0 < 0.5) and time.time() - t0 < timeout:
            batch = 16
            p.stdin.write((line + "\n") * batch)
            p.stdin.flush()
            for _ in range(batch):
                p.stdout.readline()
            n += batch
        c1, t1 = proc_cpu(p.pid), time.time()
        res[f"{where}:{ev}"] = {"kind": kind, "n": n, "cpu_us": round((c1 - c0) / n * 1e6, 1),
                                "wall_us": round((t1 - t0) / n * 1e6, 1)}
    p.stdin.close()
    p.wait()
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", required=True)
    ap.add_argument("--figram", required=True)
    ap.add_argument("--only")
    ap.add_argument("--load-timeout", type=float, default=300)
    ap.add_argument("--steps", type=int, default=2000)
    ap.add_argument("--step-timeout", type=float, default=60)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--no-steps", action="store_true")
    ap.add_argument("--out")
    ap.add_argument("--label", default="")
    ap.add_argument("--max-load-s", type=float, help="fail if any load CPU exceeds this")
    ap.add_argument("--max-step-us", type=float, help="fail if any step CPU exceeds this")
    a = ap.parse_args()
    names = a.only.split(",") if a.only else DEFAULT
    bad = []
    with tempfile.TemporaryDirectory() as tmp:
        for name in names:
            mk, events = SCEN[name]
            text = mk(a.figram)
            doc = os.path.join(tmp, name + ".bot.json")
            with open(doc, "w") as f:
                f.write(text)
            row = {"label": a.label, "scenario": name, "doc_bytes": len(text.encode()),
                   "loadavg": os.getloadavg()[0]}
            rc, cpu, wall, out = run_wait4([a.engine, doc], '{"id":0,"op":"hello"}\n', a.load_timeout)
            row["load"] = {"rc": rc, "cpu_s": cpu, "wall_s": wall,
                           "states": (json.loads(out.splitlines()[0]).get("states") if out else None)}
            if rc is None:
                row["load"]["status"] = "TIMEOUT"
            if a.max_load_s is not None and (cpu is None or cpu > a.max_load_s):
                bad.append(f"{name}: load {cpu} s > {a.max_load_s} s")
            if a.check:
                rc2, cpu2, wall2, out2 = run_wait4([a.figram, "check", doc], "", a.load_timeout)
                row["check"] = {"rc": rc2, "cpu_s": cpu2, "wall_s": wall2, "out": out2.strip()[:80]}
            if rc == 0 and not a.no_steps:
                row["step"] = step_cost(a.engine, doc, events, a.steps, a.step_timeout, last_leaf(text))
                if a.max_step_us is not None:
                    for k, v in row["step"].items():
                        if isinstance(v, dict) and v["cpu_us"] > a.max_step_us:
                            bad.append(f"{name}: step {k} {v['cpu_us']} us > {a.max_step_us} us")
            print(json.dumps(row, ensure_ascii=False), flush=True)
            if a.out:
                with open(a.out, "a") as f:
                    f.write(json.dumps(row, ensure_ascii=False) + "\n")
    for b in bad:
        print("THRESHOLD:", b)
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Run the whole host-python audit: corpus check, correct-host runs (outputs for Go-vs-Python
comparison) and the RED matrix (each invariant must turn red on a deliberately broken host).

    python3 suite.py ok   [scenario ...]
    python3 suite.py red  [variant:scenario ...]
    python3 suite.py matrix          # summarise LOGS/red/*.json into out/python/red_matrix.json
Every runner invocation goes through /home/user/figram-audit/run.sh (command, exit code, stdout, stderr).
"""
import glob
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SHARED = "/home/user/figram-audit/hosts/shared"
OUT = "/home/user/figram-audit/hosts/out/python"
AG = "/home/user/figram-audit/agents/host-python"
WORK = os.environ.get("HOST_WORK", "/tmp/claude-0/-home-user-figram-fsm-library/57129652-0dc1-5f90-93b6-ee8cd24dbb93/scratchpad/work")
RUN = "/home/user/figram-audit/run.sh"
ENGINE = os.path.abspath(os.path.join(HERE, "..", "..", "bin", "figram-engine"))

OK = ["order-1", "order-2", "booking-1", "booking-2", "support-1", "reminders-1", "reminders-2", "migration-1",
      "failures-1", "failures-2", "adversarial-1", "load_u1-7", "load_u10-7", "load_u100-7", "load_u1000-7"]

RED = [
    ("no-dedup", "support-1", []), ("no-dedup", "order-1", []),
    ("ack-before-commit", "order-1", []), ("ack-before-commit", "failures-1", []),
    ("outbox-after-ack", "order-1", []), ("outbox-after-ack", "red_outbox_after_ack-1", ["--no-ref"]),
    ("", "red_outbox_after_ack-1", ["--no-ref"]),
    ("const-guards", "order-1", []), ("const-guards", "booking-1", []), ("const-guards", "reminders-1", []),
    ("const-guards", "support-1", []), ("const-guards,no-capacity-check", "booking-1", []),
    ("no-drain", "order-1", []), ("no-drain", "support-1", []),
    ("no-resource-lock", "booking-1", []), ("no-resource-lock", "booking-2", []),
    ("seq-keys-start", "order-1", []), ("seq-keys-start", "reminders-1", []),
    ("random-keys", "order-1", []), ("random-keys", "support-1", []),
    ("no-user-lock,no-dedup", "support-1", []), ("no-user-lock", "support-1", []),
    ("stale-token-lookup", "reminders-1", []),
    ("no-validate", "adversarial-1", []),
    ("memory-timers", "reminders-1", []),
    ("trust-snapshot", "adversarial-1", []),
    ("no-crash-points", "order-1", []),
    ("no-ack-retry", "load_u1000-7", ["--busy-ms", "300", "--no-ref", "--window", "64", "--engines", "3"]),
    ("", "failures-1", ["--busy-ms", "8000"]),      # db lock shorter than the busy timeout: no NACK -> meta RED
    ("", "meta_vacuous-1", ["--no-ref"]),            # faults armed with no traffic after them -> meta RED
]


def runner_cmd(corpus, out, broken="", extra=()):
    cmd = ["nice", "-n", "10", "timeout", "900", "prlimit", "--as=8000000000", "--",
           sys.executable, "-I", os.path.join(HERE, "runner.py"), corpus, "--out", out, "--engine", ENGINE,
           "--bots", os.path.join(SHARED, "bots")]
    if broken:
        cmd += ["--broken", broken]
    return cmd + list(extra)


def corpus_path(sc):
    p = os.path.join(SHARED, "corpus", sc + ".jsonl")
    if not os.path.exists(p):
        p = os.path.join(AG, "REPRODUCTIONS", sc + ".jsonl")
    return p


def run_ok(names):
    env = dict(os.environ, HOST_WORK=WORK)
    for sc in names:
        extra = []
        if sc.startswith("load_"):
            extra = ["--no-ref", "--window", "64", "--engines", "3"]
        cmd = runner_cmd(corpus_path(sc), os.path.join(OUT, sc + ".json"), "", extra)
        rc = subprocess.call([RUN, os.path.join(AG, "LOGS", "ok", sc)] + cmd, env=env)
        print(sc, "rc", rc, flush=True)


def run_red(items):
    env = dict(os.environ, HOST_WORK=WORK)
    os.makedirs(os.path.join(AG, "LOGS", "red"), exist_ok=True)
    for variant, sc, extra in items:
        tag = (variant or "correct") + "__" + sc + ("__" + "_".join(x.strip("-") for x in extra if x.startswith("--busy"))
                                                    if any(x.startswith("--busy") for x in extra) else "")
        ref = os.path.join(WORK, "ok-" + sc, "ref")
        ex = list(extra)
        if "--no-ref" not in ex and os.path.isdir(ref):
            ex += ["--ref", ref]
        cmd = runner_cmd(corpus_path(sc), os.path.join(AG, "LOGS", "red", tag + ".json"), variant, ex)
        rc = subprocess.call([RUN, os.path.join(AG, "LOGS", "red", tag)] + cmd, env=env)
        print(tag, "rc", rc, flush=True)


def matrix():
    rows = []
    turned = {}
    for f in sorted(glob.glob(os.path.join(AG, "LOGS", "red", "*.json"))):
        d = json.load(open(f))
        fails = sorted(k for k, v in d["invariants"].items() if v != "PASS")
        tag = os.path.basename(f)[:-5]
        rows.append({"run": tag, "broken": d.get("broken"), "scenario": f"{d['scenario']}-{d['seed']}", "red": fails})
        for k in fails:
            turned.setdefault(k, []).append(tag)
    allinv = set()
    for f in glob.glob(os.path.join(OUT, "*.json")):
        if os.path.basename(f) == "red_matrix.json":
            continue
        allinv |= set(json.load(open(f))["invariants"])
    never = sorted(allinv - set(turned))
    res = {"runs": rows, "invariant_turned_red_by": turned, "invariants_never_red": never}
    json.dump(res, open(os.path.join(OUT, "red_matrix.json"), "w"), indent=1, ensure_ascii=False)
    for r in rows:
        print(f"{r['run']:<55} RED: {', '.join(r['red']) or '-- (nothing failed)'}")
    print("invariants never shown red:", never)


if __name__ == "__main__":
    what = sys.argv[1]
    args = sys.argv[2:]
    if what == "ok":
        run_ok(args or OK)
    elif what == "red":
        sel = RED if not args else [x for x in RED if f"{x[0]}:{x[1]}" in args]
        run_red(sel)
    elif what == "matrix":
        matrix()

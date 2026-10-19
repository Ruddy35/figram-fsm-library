#!/usr/bin/env python3
"""Corpus runner: plays "Telegram" (delivers updates, redelivers until acked), the operator
(control lines) and the fault injector (kill -9 points, DB lock, external fault modes, engine
faults, corrupted snapshots written straight into the DB). It restarts the host process from
the same DB file after every crash, then checks invariants (checker.py) against an independent
reference: the same corpus without fault lines, run by the CORRECT host.

    python3 runner.py CORPUS.jsonl --out OUT.json [--broken VARIANT] [--ref REF_DIR]
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import checker  # noqa: E402
import extsim  # noqa: E402

FAULT_CTLS = {"crash", "restart_host", "db_lock", "engine_fault", "external"}
ADVERSARIAL_CTLS = {"corrupt_snapshot"}
MAX_INT = 9007199254740991


def strip_clean(items):
    out = []
    for it in items:
        if it.get("ctl") in FAULT_CTLS:
            continue
        it = dict(it)
        it.pop("dup", None)
        it.pop("delay_ms", None)
        if it.get("ctl") == "timer_mode":
            it.pop("dup", None)
            if len(it) == 1:
                continue
        out.append(it)
    return out


def strip_pristine(items):
    return [it for it in strip_clean(items) if "raw" not in it and "raw_text" not in it
            and it.get("ctl") not in ADVERSARIAL_CTLS]


class HostDied(Exception):
    pass


class Runner:
    def __init__(self, items, work, opts, label):
        self.items = items
        self.work = work
        self.o = opts
        self.label = label
        self.init = items[0] if items and items[0].get("ctl") == "init" else {}
        self.family = self.init.get("bot", "pizza")
        self.db = os.path.join(work, "host.sqlite")
        self.ext = os.path.join(work, "ext.sqlite")
        self.lock = threading.Condition()
        self.send_lock = threading.Lock()
        self.pending: dict[int, dict] = {}       # d -> delivery
        self.user_busy: Counter = Counter()
        self.next_d = 1
        self.acks: list = []
        self.lat: list = []
        self.nacks = 0
        self.ctl_results: dict[int, dict] = {}
        self.host = None
        self.host_gen = 0
        self.stopping = False
        self.exits: list = []                    # (gen, returncode, expected)
        self.crash_armed = 0
        self.armed: list = []                    # armed crash points not fired yet (re-armed after restarts)
        self.crash_fired: list = []
        self.expected_stop: set = set()
        self.abandoned: list = []
        self.restarts = 0
        self.clock = 0
        self.fault_log: list = []
        self.db_lock_threads: list = []
        os.makedirs(work, exist_ok=True)
        self.stderr = open(os.path.join(work, "host.stderr"), "a")

    # ------------------------------------------------------------ host process
    def host_cmd(self):
        o = self.o
        cmd = [sys.executable, "-I", os.path.join(HERE, "host.py"), "--db", self.db, "--ext", self.ext,
               "--bots", o.bots, "--engine", o.engine, "--family", self.family, "--workers", str(o.workers),
               "--engines", str(o.engines), "--executors", str(o.executors), "--seed", str(o.seed),
               "--sync", o.sync, "--busy-ms", str(o.busy_ms)]
        if o.engine_proxy or self.init.get("engine_proxy"):
            cmd += ["--engine-proxy", os.path.join(HERE, "evil_proxy.py")]
        if o.broken:
            cmd += ["--broken", o.broken]
        return cmd

    def start_host(self):
        for attempt in range(20):  # the DB may be locked by an injected db_lock right now
            p = subprocess.Popen(self.host_cmd(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                 stderr=self.stderr, text=True, bufsize=1)
            line = p.stdout.readline()
            if line:
                break
            rc = p.wait()
            self.exits.append({"gen": -1, "rc": rc, "expected": True, "startup_failure": True})
            time.sleep(0.5)
        else:
            raise RuntimeError(f"host did not start, see {self.work}/host.stderr")
        m = json.loads(line)
        assert m["t"] == "ready", m
        self.host_gen += 1
        gen = self.host_gen
        self.host = p
        threading.Thread(target=self.reader, args=(p, gen), daemon=True).start()
        return p

    def reader(self, p, gen):
        for line in p.stdout:
            try:
                m = json.loads(line)
            except ValueError:
                continue
            self.on_msg(m)
        rc = p.wait()
        with self.lock:
            expected = gen in self.expected_stop
            self.exits.append({"gen": gen, "rc": rc, "expected": expected})
            if not expected:
                self.host = None
            self.lock.notify_all()

    def on_msg(self, m):
        with self.lock:
            t = m.get("t")
            if t == "ack":
                dl = self.pending.pop(m["d"], None)
                if dl is not None:
                    dl["copies"] -= 1
                    self.acks.append({"d": m["d"], "line": dl["line"], "user": dl.get("user"),
                                      "uid": dl.get("uid"), "status": m["status"], "raw": dl.get("raw", False)})
                    self.lat.append((m.get("lat_us", 0), m.get("step_us", 0),
                                     int((time.perf_counter() - dl["t"]) * 1e6)))
                    if dl["copies"] == 0 and dl.get("user"):
                        self.user_busy[dl["user"]] -= 1
            elif t == "nack":
                dl = self.pending.pop(m["d"], None)
                self.nacks += 1
                if dl is not None:
                    dl["nacks"] = dl.get("nacks", 0) + 1
                    if dl["nacks"] > self.o.max_nacks:
                        # Telegram would keep retrying for a day; the runner gives up and records it
                        self.abandoned.append({"line": dl["line"], "user": dl.get("user"), "uid": dl.get("uid"),
                                               "why": m.get("why")})
                        if dl.get("user"):
                            self.user_busy[dl["user"]] -= 1
                    else:
                        threading.Timer(0.03, self.redeliver, (dl,)).start()
            elif t == "done":
                self.ctl_results[m["d"]] = m.get("r", {})
            elif t == "crashing":
                key = (m["at"], m.get("on"))
                if key in self.armed:
                    self.armed.remove(key)
                self.crash_fired.append(m)
            self.lock.notify_all()

    def _send(self, obj):
        with self.send_lock:
            p = self.host
            if p is None:
                return False
            try:
                p.stdin.write(json.dumps(obj, ensure_ascii=False) + "\n")
                p.stdin.flush()
                return True
            except (BrokenPipeError, OSError, ValueError):
                return False

    def _new_d(self):
        d = self.next_d
        self.next_d += 1
        return d

    def deliver(self, dl):
        with self.lock:
            d = self._new_d()
            dl["d"] = d
            dl["t"] = time.perf_counter()
            self.pending[d] = dl
        msg = {"t": "update", "d": d}
        if "text" in dl:
            msg["text"] = dl["text"]
        else:
            msg["body"] = dl["body"]
        self._send(msg)

    def redeliver(self, dl):
        dl = dict(dl)
        dl["attempt"] = dl.get("attempt", 0) + 1
        self.deliver(dl)

    def recover_if_dead(self):
        """Host died (kill -9 at an armed point, or unexpectedly): restart it from the same DB and
        redeliver every update that was not acknowledged."""
        with self.lock:
            if self.host is not None or self.stopping:
                return False
            lost = list(self.pending.values())
            self.pending.clear()
        self.restarts += 1
        self.start_host()
        self.rearm()
        for dl in lost:
            self.redeliver(dl)
        return True

    def rearm(self):
        for at, on in list(self.armed):
            self._ctl_raw({"ctl": "crash", "at": at, "on": on})

    def _ctl_raw(self, cmd):
        with self.lock:
            d = self._new_d()
        self._send({"t": "ctl", "d": d, "c": cmd})
        self.wait(lambda: d in self.ctl_results, what=f"ctl {cmd}")
        self.ctl_results.pop(d)

    def wait(self, pred, timeout=180, what=""):
        end = time.monotonic() + timeout
        while True:
            with self.lock:
                if pred():
                    return
                dead = self.host is None and not self.stopping
                if not dead:
                    self.lock.wait(0.05)
            if dead:
                self.recover_if_dead()
            if time.monotonic() > end:
                raise RuntimeError(f"[{self.label}] timeout waiting for {what}; pending={len(self.pending)}")

    def ctl(self, cmd, timeout=180):
        """Send a control command; if the host dies while executing it, restart and resend
        (all host control commands are idempotent)."""
        while True:
            with self.lock:
                d = self._new_d()
            gen = self.host_gen
            self._send({"t": "ctl", "d": d, "c": cmd})
            try:
                self.wait(lambda: d in self.ctl_results or self.host_gen != gen or self.host is None,
                          timeout, f"ctl {cmd}")
            except RuntimeError:
                raise
            if d in self.ctl_results:
                r = self.ctl_results.pop(d)
                if "error" in r:
                    raise RuntimeError(f"[{self.label}] ctl {cmd} failed: {r['error']}")
                return r
            self.recover_if_dead()

    def quiesce(self):
        self.wait(lambda: not self.pending, what="all updates acked")
        return self.ctl({"ctl": "barrier"})

    # ------------------------------------------------------------ faults done by the outside world
    def db_lock(self, ms):
        ready = threading.Event()

        def hold():
            c = sqlite3.connect(self.db, timeout=30, isolation_level=None)
            c.execute("BEGIN EXCLUSIVE")
            ready.set()
            time.sleep(ms / 1000.0)
            c.execute("COMMIT")
            c.close()
        t = threading.Thread(target=hold, daemon=True)
        t.start()
        ready.wait(30)
        self.db_lock_threads.append(t)

    def corrupt_snapshot(self, user, mode):
        c = sqlite3.connect(self.db, timeout=30, isolation_level=None)
        row = c.execute("SELECT snapshot, hw_seq FROM users WHERE user=?", (user,)).fetchone()
        if row is None:
            c.close()
            return
        snap = json.loads(row[0])
        hw = row[1]
        if mode == "garbage":
            txt = row[0][: len(row[0]) // 2]
        elif mode == "not_object":
            txt = "[1,2,3]"
        elif mode == "unknown_field":
            txt = json.dumps({**snap, "evil": 1})
        elif mode == "bad_version":
            txt = json.dumps({**snap, "v": 2})
        elif mode == "wrong_type":
            txt = json.dumps({**snap, "seq": str(hw)})
        elif mode == "missing_at":
            s = dict(snap)
            s.pop("at", None)
            txt = json.dumps(s)
        elif mode == "huge_seq":
            txt = json.dumps({**snap, "seq": MAX_INT})
        elif mode == "rollback":
            prev = c.execute("SELECT at, seq FROM transitions WHERE user=? AND changed=1 AND seq<? ORDER BY n DESC "
                             "LIMIT 1 OFFSET 1", (user, hw)).fetchone()
            txt = json.dumps({"at": prev[0], "seq": prev[1]} if prev else {"at": snap["at"], "seq": max(0, hw - 2)})
        elif mode == "deleted_node":
            txt = json.dumps({**snap, "at": "no_such_message"})
        elif mode == "null":
            txt = None
        else:
            raise RuntimeError(f"unknown corrupt mode {mode}")
        c.execute("UPDATE users SET snapshot=? WHERE user=?", (txt, user))
        c.close()

    # ------------------------------------------------------------ corpus
    def run(self):
        os.makedirs(self.work, exist_ok=True)
        for f in ("host.sqlite", "host.sqlite-wal", "host.sqlite-shm", "ext.sqlite", "ext.sqlite-wal", "ext.sqlite-shm"):
            if os.path.exists(os.path.join(self.work, f)):
                os.remove(os.path.join(self.work, f))
        extsim.init_db(self.ext)
        t_start = time.monotonic()
        self.start_host()
        window = self.o.window
        for lineno, it in enumerate(self.items):
            if it.get("ctl") == "init":
                continue
            if "ctl" in it:
                self.do_ctl(it, lineno)
                continue
            if "raw" in it or "raw_text" in it:
                self.wait(lambda: len(self.pending) < window, what="window")
                dl = {"line": lineno, "raw": True, "copies": 1}
                if "raw_text" in it:
                    dl["text"] = it["raw_text"]
                else:
                    dl["body"] = it["raw"]
                self.deliver(dl)
                continue
            user = it["user"]
            self.wait(lambda: self.user_busy[user] == 0 and len(self.pending) < window, what=f"user {user}")
            if it.get("delay_ms"):
                time.sleep(it["delay_ms"] / 1000.0)
            body = {k: it[k] for k in ("user", "update_id", "event", "arg") if k in it}
            n = int(it.get("dup", 1))
            with self.lock:
                self.user_busy[user] += n      # the user is free again when every copy is acked
            dl = {"line": lineno, "user": user, "uid": it["update_id"], "body": body, "copies": 1}
            for _ in range(n):
                self.deliver(dict(dl, group=lineno))
        self.quiesce()
        self.ctl({"ctl": "executor", "mode": "resume"})
        self.quiesce()
        for t in self.db_lock_threads:
            t.join(60)
        self.quiesce()
        stats = self.stop_host()
        elapsed = time.monotonic() - t_start
        before = checker.dump_state(self.work)
        # restart check: a fresh host process on the same DB must not change anything
        self.start_host()
        self.quiesce()
        stats2 = self.stop_host()
        after = checker.dump_state(self.work)
        return {"stats": stats, "stats_after_restart": stats2, "elapsed_s": elapsed,
                "restart_before": before, "restart_after": after}

    def stop_host(self):
        with self.lock:
            self.stopping = True
            self.expected_stop.add(self.host_gen)
            d = self._new_d()
        self._send({"t": "ctl", "d": d, "c": {"ctl": "stop"}})
        self.wait(lambda: d in self.ctl_results, what="stop")
        r = self.ctl_results.pop(d)
        p = self.host
        p.wait(60)
        with self.lock:
            self.host = None
            self.stopping = False
        return r

    def do_ctl(self, it, lineno):
        k = it["ctl"]
        self.quiesce()
        if k == "crash":
            self.crash_armed += 1
            self.armed.append((it["at"], it.get("on")))
            self.ctl({"ctl": "crash", "at": it["at"], "on": it.get("on")})
            self.fault_log.append({"line": lineno, "fault": "crash", "at": it["at"], "on": it.get("on")})
        elif k == "restart_host":
            self.stop_host()
            self.restarts += 1
            self.start_host()
            self.rearm()
        elif k == "upgrade":
            self.ctl({"ctl": "upgrade", "bot": it["bot"]})
        elif k == "advance_ms":
            self.clock += int(it["ms"])
            self.ctl({"ctl": "advance_to", "t": self.clock})
        elif k == "external":
            extsim.set_mode(self.ext, it.get("target", "all"), it["mode"], int(it.get("count", 1)), int(it.get("ms", 0)))
            self.fault_log.append({"line": lineno, "fault": "external", **{x: it[x] for x in it if x != "ctl"}})
        elif k == "executor":
            self.ctl({"ctl": "executor", "mode": it["mode"]})
        elif k == "timer_mode":
            self.ctl({"ctl": "timer_mode", **{x: it[x] for x in ("dup", "ignore_cancel") if x in it}})
        elif k == "replay_stale":
            self.ctl({"ctl": "replay_stale", "user": it["user"]})
        elif k == "reset_user":
            self.ctl({"ctl": "reset_user", "user": it["user"]})
        elif k == "db_lock":
            self.db_lock(int(it["ms"]))
            self.fault_log.append({"line": lineno, "fault": "db_lock", "ms": it["ms"]})
        elif k == "engine_fault":
            self.ctl({"ctl": "engine_fault", "mode": it["mode"]})
            self.fault_log.append({"line": lineno, "fault": "engine_fault", "mode": it["mode"]})
        elif k == "corrupt_snapshot":
            self.corrupt_snapshot(it["user"], it["mode"])
        elif k in ("barrier", "note"):
            pass
        else:
            raise RuntimeError(f"unknown ctl line {it}")


def percentiles(xs):
    if not xs:
        return {}
    xs = sorted(xs)

    def p(q):
        return xs[min(len(xs) - 1, int(q * len(xs)))]
    return {"n": len(xs), "p50": p(0.50), "p95": p(0.95), "p99": p(0.99), "max": xs[-1]}


def run_one(items, work, opts, label):
    r = Runner(items, work, opts, label)
    res = r.run()
    res["runner"] = r
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("corpus")
    ap.add_argument("--out", required=True)
    ap.add_argument("--work", default=None)
    ap.add_argument("--bots", default="/home/user/figram-audit/hosts/shared/bots")
    ap.add_argument("--engine", default=os.path.abspath(os.path.join(HERE, "..", "..", "bin", "figram-engine")))
    ap.add_argument("--broken", default="")
    ap.add_argument("--ref", default=None, help="reuse reference run directory (clean/pristine) of the correct host")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--engines", type=int, default=2)
    ap.add_argument("--executors", type=int, default=4)
    ap.add_argument("--window", type=int, default=32)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--sync", default="FULL")
    ap.add_argument("--busy-ms", type=int, default=3000)
    ap.add_argument("--engine-proxy", action="store_true")
    ap.add_argument("--no-ref", action="store_true")
    ap.add_argument("--max-nacks", type=int, default=150)
    o = ap.parse_args()
    items = [json.loads(x) for x in open(o.corpus) if x.strip()]
    name = os.path.basename(o.corpus).rsplit(".", 1)[0]
    work = o.work or os.path.join(os.environ.get("HOST_WORK", "/tmp/hostpy-work"), (o.broken or "ok") + "-" + name)
    shutil.rmtree(work, ignore_errors=True)
    os.makedirs(work)
    main_res = run_one(items, os.path.join(work, "run"), o, "run")
    refs = {}
    if not o.no_ref:
        ref_dir = o.ref or os.path.join(work, "ref")
        ref_opts = argparse.Namespace(**{**vars(o), "broken": ""})
        for kind, fn in (("clean", strip_clean), ("pristine", strip_pristine)):
            d = os.path.join(ref_dir, kind)
            if kind == "pristine" and fn(items) == strip_clean(items):
                refs[kind] = refs.get("clean")
                continue
            if not (o.ref and os.path.exists(os.path.join(d, "host.sqlite"))):
                os.makedirs(d, exist_ok=True)
                run_one(fn(items), d, ref_opts, kind)
            refs[kind] = d
    r = main_res["runner"]
    out = checker.build_output(os.path.join(work, "run"), items, name, r, main_res, refs, o)
    os.makedirs(os.path.dirname(os.path.abspath(o.out)), exist_ok=True)
    with open(o.out, "w") as f:
        json.dump(out, f, ensure_ascii=False, indent=1, sort_keys=False)
    fails = [k for k, v in out["invariants"].items() if v != "PASS"]
    print(json.dumps({"scenario": name, "broken": o.broken or None, "elapsed_s": round(main_res["elapsed_s"], 2),
                      "fails": fails, "counts": out["counts"], "latency": out.get("latency")}, ensure_ascii=False))
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()

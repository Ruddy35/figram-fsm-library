#!/usr/bin/env python3
"""Driver of a hostpy run: simulated Telegram users, the external systems' own queues, the
operator (crashes, a restore from a backup, snapshot loss, resets, a document update), then
quiescence and the invariants of check.py.

The host runs as a child process (host.py) and is restarted whenever it exits (injected crashes
exit with status 70 at a named boundary). Users only see the chat (the messages the host sent)
and press buttons of the message they see, like real users; they keep pressing while a wait is
pending.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import signal
import sqlite3
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("FIGRAM_REPO", "/home/user/figram-audit-worktrees/a6-hostpy")
sys.path.insert(0, HERE)
import ext as extmod  # noqa: E402
import check as checkmod  # noqa: E402
from host import connect, Tx, init_dbs  # noqa: E402

ENGINE = os.path.join(REPO, ".lake/build/bin/figram-engine")
BOTS = os.path.join(HERE, "bots")


# --------------------------------------------------------------------------- users
class User:
    def __init__(self, name: str, rng: random.Random) -> None:
        self.name = name
        self.rng = rng
        self.screen = None
        self.last_id = 0
        self.changed_at = 0.0
        self.next_at = 0.0
        self.actions = 0
        self.stopped = False
        self.c = {}            # counters per bot
        self.started = False


def btn(data, **kw):
    return {"type": "button", "data": data, **kw}


def cmd(text):
    return {"type": "command", "text": text}


WAITING = {"placing", "settling", "confirming"}


def policy_shop(u: User, screen: str, fresh: bool):
    r, c = u.rng.random(), u.c
    if screen == "menu":
        return btn("shop")
    if screen == "catalog":
        if fresh:
            c["adds"] = u.rng.choice([0, 1, 1, 2, 3])
        if r < 0.08:
            return btn("info")
        if c.get("adds", 0) > 0:
            c["adds"] -= 1
            return btn("add", item="pizza!" if u.rng.random() < 0.1 else u.rng.choice(["margherita", "salami"]))
        return btn("checkout")
    if screen == "empty_cart":
        return btn("back")
    if screen == "info" or screen == "help":
        return btn("back")
    if screen == "confirm":
        if not fresh:
            # pressed pay and nothing happened yet: the CRM call runs before "placing" is sent
            if r < 0.3:
                return cmd("/help")
            if r < 0.4:
                return btn("cancel")      # a button of an older message
            if r < 0.5:
                return btn("status")
        return btn("pay") if r < 0.9 else btn("back")
    if screen == "placing":
        if fresh:
            return None
        if r < 0.25:
            return btn("status")
        if r < 0.35:
            return cmd("/help")
        if r < 0.40:
            return btn("cancel")
        return None
    if screen == "order_failed":
        return btn("retry") if r < 0.8 else btn("cancel")
    if screen == "done":
        c["orders"] = c.get("orders", 0) + (1 if fresh else 0)
        if c["orders"] >= 2:
            u.stopped = True
            return None
        return btn("again")
    return cmd("/start")


def policy_payouts(u: User, screen: str, fresh: bool):
    r, c = u.rng.random(), u.c
    if screen == "home":
        c.setdefault("target", u.rng.randint(2, 7))
        if c.get("sends", 0) < c["target"]:
            if r < 0.4:                      # a double tap
                c["sends"] = c.get("sends", 0) + 2
                return [btn("send"), btn("send")]
            c["sends"] = c.get("sends", 0) + 1
            return btn("send")
        return btn("close")
    if screen == "no_funds":
        return btn("close") if r < 0.7 else btn("back")
    if screen == "idle":
        return btn("back")
    if screen == "settling":
        if fresh:
            return None
        if r < 0.3:
            return btn("send")          # no arrow on settling: ignored
        if r < 0.33:
            return cmd("/start")
        return None
    if screen == "closed":
        u.stopped = True
        return None
    return cmd("/start")


def policy_booking(u: User, screen: str, fresh: bool):
    r, c = u.rng.random(), u.c
    if screen == "start":
        return btn("book")
    if screen == "pick":
        return btn("seat", seat=f"{u.rng.choice('ABCDEF')}{u.rng.randint(1, 30)}")
    if screen == "holding":
        if fresh and r < 0.35:
            u.next_at = time.time() + 1.0      # let the hold timer fire
            return None
        if not fresh and c.get("confirmed_on_holding"):
            c["confirmed_on_holding"] = False
            if r < 0.3:
                return cmd("/help")
            if r < 0.4:
                return btn("cancel")
        c["confirmed_on_holding"] = True
        return btn("confirm")
    if screen == "confirming":
        if fresh:
            return None
        if r < 0.15:
            return cmd("/help")
        if r < 0.19:
            return btn("cancel")
        return None
    if screen == "help":
        return btn("back")
    if screen == "expired":
        return btn("again")
    if screen == "booked":
        if fresh:
            c["booked"] = c.get("booked", 0) + 1
        if r < 0.3 and c.get("resends", 0) < 2:
            c["resends"] = c.get("resends", 0) + 1
            return btn("resend")
        if c.get("booked", 0) >= 2:
            u.stopped = True
            return None
        return btn("again")
    if screen == "book_failed":
        return btn("retry") if r < 0.7 else btn("back")
    return cmd("/start")


POLICIES = {"shop": policy_shop, "payouts": policy_payouts, "booking": policy_booking}


class Telegram:
    """The update side of the simulated Telegram: deliveries (getUpdates) and the chat."""

    def __init__(self, path: str, rng: random.Random, dup_p: float) -> None:
        self.c = connect(path)
        self.rng = rng
        self.dup_p = dup_p
        self.next_update = 1000
        self.late: list = []
        self.lock = threading.Lock()
        self.sent = 0
        self.dups = 0

    def send(self, user: str, payload: dict) -> None:
        with self.lock:
            uid = self.next_update
            self.next_update += 1
        body = json.dumps(payload)
        with Tx(self.c):
            self.c.execute("INSERT INTO deliveries(update_id, user, payload) VALUES(?,?,?)", (uid, user, body))
            if self.rng.random() < self.dup_p:
                self.c.execute("INSERT INTO deliveries(update_id, user, payload) VALUES(?,?,?)", (uid, user, body))
                self.dups += 1
        if self.rng.random() < self.dup_p / 2:
            with self.lock:
                self.late.append((time.time() + self.rng.uniform(0.3, 2.0), uid, user, body))
        self.sent += 1

    def flush_late(self) -> None:
        now = time.time()
        with self.lock:
            due = [x for x in self.late if x[0] <= now]
            self.late = [x for x in self.late if x[0] > now]
        for _, uid, user, body in due:
            with Tx(self.c):
                self.c.execute("INSERT INTO deliveries(update_id, user, payload) VALUES(?,?,?)", (uid, user, body))
            self.dups += 1


def init_tg(path: str) -> None:
    c = connect(path)
    c.executescript("""
CREATE TABLE IF NOT EXISTS deliveries(seq INTEGER PRIMARY KEY AUTOINCREMENT, update_id INTEGER, user TEXT, payload TEXT);
CREATE TABLE IF NOT EXISTS chat(id INTEGER PRIMARY KEY AUTOINCREMENT, user TEXT, message TEXT, key TEXT, at REAL);
CREATE INDEX IF NOT EXISTS chat_user ON chat(user, id);
""")
    c.close()


# --------------------------------------------------------------------------- the host process
class HostProc:
    def __init__(self, a, run_dir: str) -> None:
        self.a = a
        self.dir = run_dir
        self.p = None
        self.restarts = 0
        self.crashes = 0
        self.doc = a.doc_v1
        self.docv = 1
        self.log = open(os.path.join(run_dir, "host.stderr"), "ab")
        self.mem = {"host_max_kb": 0, "engine_max_kb": 0, "samples": []}
        self.cpu: dict = {}           # pid → {"host"|"engine": cpu seconds at the last sample}

    def start(self) -> None:
        cmdline = [sys.executable, os.path.join(HERE, "host.py"), "--dir", self.dir, "--doc", self.doc,
                   "--docv", str(self.docv), "--seed", str(self.a.seed + self.restarts),
                   "--crash-p", str(self.a.crash_p), "--workers", str(self.a.workers),
                   "--executors", str(self.a.executors), "--engine", self.a.engine]
        if self.a.crash_points:
            cmdline += ["--crash-points", self.a.crash_points]
        if self.a.sched_outside:
            cmdline.append("--sched-outside")
        for f in self.a.flag:
            cmdline += ["--flag", f]
        self.p = subprocess.Popen(cmdline, stderr=self.log, stdout=self.log, start_new_session=True)
        self.restarts += 1

    def alive(self) -> bool:
        return self.p is not None and self.p.poll() is None

    def kill(self) -> None:
        if self.p and self.p.poll() is None:
            try:
                os.killpg(self.p.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            self.p.wait()

    def stop(self) -> None:
        if self.p and self.p.poll() is None:
            self.p.send_signal(signal.SIGTERM)
            try:
                self.p.wait(30)
            except subprocess.TimeoutExpired:
                self.kill()
        # engine children die with their stdin

    def watch(self) -> None:
        if self.p is not None and self.p.poll() is not None:
            rc = self.p.returncode
            if rc == 70:
                self.crashes += 1
            else:
                self.log.write(f"\n[driver] host exited with {rc}\n".encode())
                self.log.flush()
            self.start()

    def sample_mem(self) -> None:
        try:
            pids = json.load(open(os.path.join(self.dir, "host.pids")))
        except Exception:
            return

        def rss(pid):
            try:
                for line in open(f"/proc/{pid}/status"):
                    if line.startswith("VmRSS:"):
                        return int(line.split()[1])
            except OSError:
                return 0
            return 0
        h = rss(pids["host"])
        e = [rss(p) for p in pids["engines"]]

        def cpu(pid):
            try:
                f = open(f"/proc/{pid}/stat").read().rsplit(")", 1)[1].split()
                return (int(f[11]) + int(f[12])) / os.sysconf("SC_CLK_TCK")
            except (OSError, IndexError, ValueError):
                return None
        hc = cpu(pids["host"])
        if hc is not None:
            self.cpu.setdefault(pids["host"], {})["host"] = hc
        for p in pids["engines"]:
            ec = cpu(p)
            if ec is not None:
                self.cpu.setdefault(p, {})["engine"] = ec
        self.mem["host_max_kb"] = max(self.mem["host_max_kb"], h)
        self.mem["engine_max_kb"] = max([self.mem["engine_max_kb"]] + e)
        self.mem["samples"].append((round(time.time(), 1), h, e))


# --------------------------------------------------------------------------- the run
def counts(run_dir: str) -> dict:
    H = connect(os.path.join(run_dir, "host.db"))
    out = {
        "inbound": H.execute("SELECT COUNT(*) FROM inbound").fetchone()[0],
        "outbox_new": H.execute("SELECT COUNT(*) FROM outbox WHERE state='new'").fetchone()[0],
        "timers": H.execute("SELECT COUNT(*) FROM timers").fetchone()[0],
        "steps": H.execute("SELECT COUNT(*) FROM elog").fetchone()[0],
    }
    H.close()
    sp = os.path.join(run_dir, "sched.db")
    if os.path.exists(sp):
        S = connect(sp)
        out["timers"] += S.execute("SELECT COUNT(*) FROM timers").fetchone()[0]
        S.close()
    T = connect(os.path.join(run_dir, "tg.db"))
    out["deliveries"] = T.execute("SELECT COUNT(*) FROM deliveries").fetchone()[0]
    T.close()
    X = connect(os.path.join(run_dir, "ext.db"))
    out["delayed"] = X.execute("SELECT COUNT(*) FROM delayed WHERE done=0").fetchone()[0]
    out["callbacks"] = X.execute("SELECT COALESCE(MAX(id),0) FROM callbacks").fetchone()[0]
    X.close()
    H = connect(os.path.join(run_dir, "host.db"))
    r = H.execute("SELECT v FROM meta WHERE k='cb_offset'").fetchone()
    out["cb_pending"] = out["callbacks"] - (int(r[0]) if r else 0)
    H.close()
    return out


def latency(run_dir: str) -> dict:
    """Receive (inbound) → commit of the step, per kind of event."""
    H = connect(os.path.join(run_dir, "host.db"))
    out = {}
    for kind in ("tg", "outcome", "timer"):
        xs = sorted(1000 * (a - r) for a, r in H.execute(
            "SELECT at, recv FROM elog WHERE kind=? AND recv IS NOT NULL AND op IN ('step','start')", (kind,)))
        if xs:
            out[kind] = {"n": len(xs), "p50": round(xs[len(xs) // 2], 1), "p90": round(xs[int(len(xs) * .9)], 1),
                         "p99": round(xs[int(len(xs) * .99)], 1), "max": round(xs[-1], 1)}
    H.close()
    return out


def divergence(run_dir: str) -> dict:
    """Users whose last chat message is not the message of their snapshot (what they see vs where
    the bot thinks they are), and users whose last chat message is a waiting message."""
    H = connect(os.path.join(run_dir, "host.db"))
    T = connect(os.path.join(run_dir, "tg.db"))
    snaps = {u: json.loads(s)["at"] for u, s in H.execute("SELECT user, snap FROM snapshots")}
    last = {u: m for u, i, m in T.execute("SELECT c.user, c.id, c.message FROM chat c JOIN (SELECT user, MAX(id) m "
                                         "FROM chat GROUP BY user) x ON c.id=x.m")}
    diff = [u for u in snaps if last.get(u) != snaps[u]]
    waiting = [u for u in diff if last.get(u) in WAITING]
    H.close(); T.close()
    return {"users": len(snaps), "chat_differs_from_snapshot": len(diff), "chat_shows_waiting_message": len(waiting),
            "examples": [(u, last.get(u), snaps[u]) for u in diff[:5]]}


def admin(run_dir: str, user: str, op: str, n: int) -> None:
    H = connect(os.path.join(run_dir, "host.db"))
    with Tx(H):
        H.execute("INSERT INTO inbound(user,uid,kind,event,token,run,data,at) VALUES(?,?,?,?,?,?,?,?)",
                  (user, f"admin:{op}:{n}", "admin", op, None, None, "{}", time.time()))
    H.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bot", required=True, choices=sorted(POLICIES))
    ap.add_argument("--out", required=True)
    ap.add_argument("--users", type=int, default=100)
    ap.add_argument("--duration", type=float, default=30)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--crash-p", type=float, default=0.0)
    ap.add_argument("--crash-points", default="")
    ap.add_argument("--dup-p", type=float, default=0.1)
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--executors", type=int, default=16)
    ap.add_argument("--engine", default=ENGINE)
    ap.add_argument("--flag", action="append", default=[])
    ap.add_argument("--sched-outside", action="store_true")
    ap.add_argument("--restore", default="", help="BACKUP_AT,RESTORE_AT seconds")
    ap.add_argument("--lose", type=int, default=0, help="users whose snapshot is lost")
    ap.add_argument("--restarts", type=int, default=0, help="admin restarts")
    ap.add_argument("--doc-update-at", type=float, default=0)
    ap.add_argument("--doc-v2", default="")
    ap.add_argument("--delayed-pct", type=int, default=0)
    ap.add_argument("--callback-max-s", type=float, default=4.0)
    ap.add_argument("--email-dead-pct", type=int, default=15)
    ap.add_argument("--notify-dead-pct", type=int, default=20)
    ap.add_argument("--stuck-ok", action="store_true")
    ap.add_argument("--no-replay", action="store_true")
    ap.add_argument("--quiesce-timeout", type=float, default=120)
    ap.add_argument("--script", default="", help="JSON file: scripted users and operator steps")
    ap.add_argument("--cycle", action="store_true", help="users start over after finishing (soak)")
    a = ap.parse_args()
    a.doc_v1 = os.path.join(BOTS, f"{a.bot}.bot.json")
    run_dir = a.out
    if os.path.exists(run_dir):
        shutil.rmtree(run_dir)
    os.makedirs(run_dir)
    rng = random.Random(a.seed)
    t0 = time.time()
    script = json.load(open(a.script)) if a.script else None
    extmod.init(os.path.join(run_dir, "ext.db"), {**{
        "seed": a.seed, "t0": t0, "delayed_pct": a.delayed_pct, "delay_s": 0.4,
        "email_dead_pct": a.email_dead_pct, "notify_dead_pct": a.notify_dead_pct,
        "email_down": [[2.0, 4.0]], "notify_down": [[1.0, 3.0]], "callback_s": 0.3, "callback_max_s": a.callback_max_s,
        "latency_ms": {"crm": [20, 150], "bank": [10, 80], "venue": [20, 120]},
    }, **(script or {}).get("ext", {})})
    init_tg(os.path.join(run_dir, "tg.db"))
    init_dbs(run_dir, a.sched_outside)
    host = HostProc(a, run_dir)
    host.start()
    # the clock of the scenario starts when the host is ready (its engine answered hello)
    end_wait = time.time() + 60
    while not os.path.exists(os.path.join(run_dir, "host.pids")) and time.time() < end_wait:
        time.sleep(0.05)
    t0 = time.time()
    tg = Telegram(os.path.join(run_dir, "tg.db"), rng, a.dup_p)
    chat = connect(os.path.join(run_dir, "tg.db"))
    xq = extmod.Ext(os.path.join(run_dir, "ext.db"))
    users = [User(f"u{i:04d}", random.Random(a.seed * 100003 + i)) for i in range(a.users)]
    pol = POLICIES[a.bot]
    events = []                 # (time, what, arg)
    if a.restore:
        b, r = (float(x) for x in a.restore.split(","))
        events += [(b, "backup", None), (r, "restore", None)]
    for i in range(a.lose):
        events.append((rng.uniform(0.15, 0.8) * a.duration, "lose", rng.choice(users).name))
    for i in range(a.restarts):
        events.append((rng.uniform(0.15, 0.8) * a.duration, "restart", rng.choice(users).name))
    if a.doc_update_at:
        events.append((a.doc_update_at, "docupdate", a.doc_v2))
    if script:
        users = []
        for st in script["steps"]:
            if st[1] in ("backup", "restore"):
                events.append((st[0], st[1], None))
            else:
                events.append((st[0], "send", (st[1], st[2])))
    events.sort(key=lambda x: x[0])
    log = []
    stats = {"user_actions": 0}
    n_admin = 0
    last_mem = 0.0
    t_load_end = None
    try:
        while True:
            now = time.time()
            el = now - t0
            host.watch()
            if now - last_mem > 1.0:
                host.sample_mem()
                last_mem = now
            while events and events[0][0] <= el:
                _, what, arg = events.pop(0)
                if what == "backup":
                    src = sqlite3.connect(os.path.join(run_dir, "host.db"), timeout=60)
                    dst = sqlite3.connect(os.path.join(run_dir, "host.bak.db"))
                    src.backup(dst)
                    dst.close()
                    src.close()
                    log.append((round(el, 2), "backup"))
                elif what == "restore":
                    host.kill()
                    for sfx in ("", "-wal", "-shm"):
                        if os.path.exists(os.path.join(run_dir, "host.db" + sfx)):
                            shutil.copy(os.path.join(run_dir, "host.db" + sfx), os.path.join(run_dir, "host.lost.db" + sfx))
                    for sfx in ("-wal", "-shm"):
                        p = os.path.join(run_dir, "host.db" + sfx)
                        if os.path.exists(p):
                            os.remove(p)
                    shutil.copy(os.path.join(run_dir, "host.bak.db"), os.path.join(run_dir, "host.db"))
                    host.start()
                    log.append((round(el, 2), "restore"))
                elif what in ("lose", "restart"):
                    n_admin += 1
                    admin(run_dir, arg, what, n_admin)
                    log.append((round(el, 2), what, arg))
                elif what == "send":
                    tg.send(arg[0], arg[1])
                    log.append((round(el, 2), "send", arg[0], arg[1].get("data") or arg[1].get("text")))
                elif what == "docupdate":
                    host.kill()
                    host.doc, host.docv = arg, 2
                    host.start()
                    log.append((round(el, 2), "docupdate"))
            extmod.apply_delayed(os.path.join(run_dir, "ext.db"), xq)
            tg.flush_late()
            if el < a.duration:
                rows = chat.execute("SELECT c.user, c.id, c.message FROM chat c JOIN (SELECT user, MAX(id) m FROM chat "
                                    "GROUP BY user) x ON c.id = x.m").fetchall()
                latest = {u: (i, m) for u, i, m in rows}
                for u in users:
                    if u.stopped and a.cycle and now >= getattr(u, "restart_at", 0):
                        if getattr(u, "restart_at", 0) == 0:
                            u.restart_at = now + u.rng.uniform(1.0, 3.0)
                            continue
                        u.stopped, u.c, u.actions, u.restart_at = False, {}, 0, 0
                        tg.send(u.name, cmd("/start"))
                        stats["cycles"] = stats.get("cycles", 0) + 1
                        u.next_at = now + 0.5
                        continue
                    if u.stopped:
                        continue
                    if not u.started:
                        if rng.random() < 0.2:
                            tg.send(u.name, cmd("/start"))
                            u.started = True
                            u.next_at = now + 1.0
                        continue
                    fresh = False
                    if u.name in latest and latest[u.name][0] != u.last_id:
                        u.last_id, u.screen = latest[u.name]
                        u.changed_at = now
                        u.next_at = now + u.rng.uniform(0.02, 0.15)
                        fresh = True
                        u.fresh = True
                    if now < u.next_at:
                        continue
                    fresh = getattr(u, "fresh", False)
                    u.fresh = False
                    u.same = 0 if fresh else getattr(u, "same", 0) + 1
                    if u.same >= 6 and u.screen not in WAITING or u.same >= 25:
                        # nothing happens on this screen: the user types /start
                        u.same = 0
                        tg.send(u.name, cmd("/start"))
                        u.actions += 1
                        stats["user_actions"] += 1
                        stats["frustrated_starts"] = stats.get("frustrated_starts", 0) + 1
                        u.next_at = now + 0.5
                        continue
                    if u.screen is None:
                        u.next_at = now + 1.0
                        continue
                    payload = pol(u, u.screen, fresh)
                    if u.stopped:
                        continue
                    for pl in (payload if isinstance(payload, list) else [payload] if payload else []):
                        tg.send(u.name, pl)
                        u.actions += 1
                        stats["user_actions"] += 1
                    if u.next_at <= now:
                        wait = u.rng.uniform(0.05, 0.5) if u.screen in WAITING else u.rng.uniform(0.25, 0.6)
                        u.next_at = now + wait
                    if u.actions > 80:
                        u.stopped = True
                time.sleep(0.01)
                continue
            if t_load_end is None:
                t_load_end = el
                c0 = counts(run_dir)
                stats["steps_at_load_end"] = c0["steps"]
                log.append((round(el, 2), "load end", c0))
                quiet_since = None
            c = counts(run_dir)
            busy = c["inbound"] or c["outbox_new"] or c["timers"] or c["deliveries"] or c["delayed"] \
                or c["cb_pending"] or tg.late
            if not busy and host.alive():
                quiet_since = quiet_since or now
                if now - quiet_since > 1.5:
                    break
            else:
                quiet_since = None
            if el - t_load_end > a.quiesce_timeout:
                log.append((round(el, 2), "quiescence timeout", c))
                break
            time.sleep(0.05)
    finally:
        host.sample_mem()
        host.stop()
        host.kill()
    t_end = time.time() - t0
    docs = {1: a.doc_v1}
    if a.doc_v2:
        docs[2] = a.doc_v2
    r = checkmod.check(run_dir, docs, ENGINE, {"stuck_ok": a.stuck_ok}, replay=not a.no_replay)
    load = t_load_end or a.duration
    summary = {
        "args": vars(a), "log": log, "duration_load_s": round(load, 1), "duration_total_s": round(t_end, 1),
        "user_actions": stats["user_actions"], "frustrated_starts": stats.get("frustrated_starts", 0),
        "user_cycles": stats.get("cycles", 0), "updates_sent": tg.sent, "duplicate_deliveries": tg.dups,
        "steps_at_load_end": stats.get("steps_at_load_end"), "host_restarts": host.restarts - 1,
        "host_crashes_injected": host.crashes,
        "throughput_steps_per_s_load": round((stats.get("steps_at_load_end") or 0) / load, 1),
        "mem": {k: v for k, v in host.mem.items() if k != "samples"},
        "cpu_s": {"host": round(sum(v.get("host", 0) for v in host.cpu.values()), 1),
                  "engines": round(sum(v.get("engine", 0) for v in host.cpu.values()), 1)},
        "mem_samples_tail": host.mem["samples"][-3:],
        "ok": r["ok"], "violations": r["violations"], "notes": r["notes"], "alerts": r["alerts"],
        "metrics": r["metrics"],
        "latency_ms": latency(run_dir),
        "user_visible_divergence": divergence(run_dir),
    }
    with open(os.path.join(run_dir, "summary.json"), "w") as f:
        json.dump(summary, f, indent=1, default=str)
    brief = {k: summary[k] for k in ("ok", "duration_load_s", "duration_total_s", "user_actions", "updates_sent",
                                       "duplicate_deliveries", "host_crashes_injected", "throughput_steps_per_s_load",
                                       "mem", "cpu_s", "notes", "alerts", "log", "latency_ms", "user_visible_divergence")}
    brief["violations"] = {k: {"count": v["count"], "examples": v["examples"][:2]} for k, v in r["violations"].items()}
    brief["metrics"] = {k: v for k, v in sorted(r["metrics"].items())}
    print(json.dumps(brief, indent=1, default=str))
    return 0 if r["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())

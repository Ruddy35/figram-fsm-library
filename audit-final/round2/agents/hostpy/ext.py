"""Simulated external systems of the hostpy audit host (no network: one SQLite file, ext.db).

Every service is idempotent by key: the first request with a key that takes effect stores
(result, details, run) and later requests with the same key return the stored answer without
a new effect. Effects are counted in `applications` (one row per effect actually applied), so a
broken host that changes keys between attempts is visible as two effects of one logical action.

Failure modes of a request (chosen deterministically from the key, the attempt and the seed):
  ok              — the answer comes back;
  error           — a 5xx before acting (no effect);
  timeout_before  — no answer, the request never reached the system (no effect);
  timeout_after   — the system acts and the answer is lost (effect, no answer);
  delayed         — the request is accepted into the system's own queue and applied later
                    (by `apply_delayed`, run by the driver), the caller times out; only when
                    the run enables it (`delayed` in config).
`lookup(key)` reads the result by key (reconciliation as the contract describes it);
`fence(key)` reads it and, if there is none, marks the key so that a late (delayed) request with
it is refused — the variant of reconciliation that closes the race of a request still in flight.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS effects(service TEXT, key TEXT, user TEXT, run TEXT, payload TEXT,
  result TEXT, details TEXT, at REAL, PRIMARY KEY(service, key));
CREATE TABLE IF NOT EXISTS applications(id INTEGER PRIMARY KEY AUTOINCREMENT, service TEXT, key TEXT,
  user TEXT, run TEXT, payload TEXT, at REAL);
CREATE TABLE IF NOT EXISTS fenced(service TEXT, key TEXT, at REAL, PRIMARY KEY(service, key));
CREATE TABLE IF NOT EXISTS delayed(id INTEGER PRIMARY KEY AUTOINCREMENT, service TEXT, key TEXT,
  user TEXT, action TEXT, token INTEGER, run TEXT, payload TEXT, due REAL, done INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS callbacks(id INTEGER PRIMARY KEY AUTOINCREMENT, service TEXT, key TEXT,
  user TEXT, action TEXT, token INTEGER, run TEXT, result TEXT, details TEXT, due REAL, at REAL);
CREATE TABLE IF NOT EXISTS requests(id INTEGER PRIMARY KEY AUTOINCREMENT, service TEXT, key TEXT,
  user TEXT, attempt INTEGER, mode TEXT, at REAL);
CREATE TABLE IF NOT EXISTS config(k TEXT PRIMARY KEY, v TEXT);
"""

# action → (service, positive outcome, negative outcome)
SERVICES = {
    "create_order": ("crm", "created", "rejected"),
    "transfer": ("bank", "sent", "declined"),
    "book_seat": ("venue", "booked", "full"),
    "send_receipt": ("email", "delivered", "bounced"),
    "send_confirmation": ("email", "delivered", "bounced"),
    "notify_staff": ("notify", "ok", None),
    "nudge_user": ("notify", "ok", None),
}


class Timeout(Exception):
    pass


class ServerError(Exception):
    pass


def h(*parts) -> int:
    return int.from_bytes(hashlib.sha256("|".join(map(str, parts)).encode()).digest()[:8], "big")


def connect(path: str) -> sqlite3.Connection:
    c = sqlite3.connect(path, timeout=60, isolation_level=None, check_same_thread=False)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA synchronous=NORMAL")
    c.execute("PRAGMA busy_timeout=60000")
    return c


def init(path: str, config: dict) -> None:
    c = connect(path)
    c.executescript(SCHEMA)
    for k, v in config.items():
        c.execute("INSERT OR REPLACE INTO config VALUES(?,?)", (k, json.dumps(v)))
    c.close()


class Ext:
    """Client side of the simulated systems (used by the host's executors)."""

    def __init__(self, path: str) -> None:
        self.c = connect(path)
        self.cfg = {k: json.loads(v) for k, v in self.c.execute("SELECT k, v FROM config")}
        self.seed = self.cfg.get("seed", 0)
        self.t0 = self.cfg.get("t0", time.time())

    # --- behaviour -------------------------------------------------------------------
    def mode(self, service: str, key: str, user: str, attempt: int) -> str:
        cfg, s = self.cfg, self.seed
        now = time.time() - self.t0
        if service in ("email", "notify"):
            if h("down-for-good", service, user, s) % 100 < cfg.get(f"{service}_dead_pct", 0):
                return "error"
            for a, b in cfg.get(f"{service}_down", []):
                if a <= now < b:
                    return "error"
            return "ok"
        forced = cfg.get("force_mode", {}).get(user)
        if forced:
            return forced
        r = h("mode", service, key, s) % 100
        table = [
            # (share in %, mode on the first attempt, mode on later attempts)
            (cfg.get("delayed_pct", 0), "delayed", "timeout_before"),       # applied later
            (cfg.get("timeout_after_pct", 8), "timeout_after", "timeout_after"),  # acts, answer lost
            (cfg.get("timeout_never_pct", 4), "timeout_before", "timeout_before"),  # never reaches
            (cfg.get("timeout_first_pct", 4), "timeout_before", "ok"),
            (cfg.get("flaky_pct", 10), "error", "ok"),
        ]
        for share, first, later in table:
            if r < share:
                return first if attempt == 1 else later
            r -= share
        return "ok"

    def answer(self, service: str, key: str, user: str, payload) -> tuple[str, dict]:
        """What the system answers when it acts (deterministic from the key)."""
        _, pos, neg = next(v for v in SERVICES.values() if v[0] == service)
        r = h("answer", service, key, self.seed) % 100
        if service == "crm":
            items = payload.get("items", [])
            if any(i.endswith("!") for i in items):
                return neg, {"reason": "out_of_stock"}
            if r < 8:
                return neg, {"reason": "other"}
            return pos, {"order": key}
        if service == "bank":
            return (neg, {"reason": "limit"}) if r < 10 else (pos, {"ref": key})
        if service == "venue":
            return (neg, {"reason": "full"}) if r < 15 else (pos, {"seat": payload.get("seat")})
        if service == "email":
            return (neg, {"reason": "bounce"}) if r < 10 else (pos, {})
        return pos, {}

    # --- the effect ------------------------------------------------------------------
    def _apply(self, service, key, user, run, payload, fenced_ok=False):
        """Apply the request in one transaction; return (result, details, applied_now) or None
        if the key is fenced."""
        c = self.c
        c.execute("BEGIN IMMEDIATE")
        try:
            row = c.execute("SELECT result, details FROM effects WHERE service=? AND key=?",
                            (service, key)).fetchone()
            if row:
                c.execute("COMMIT")
                return row[0], json.loads(row[1]), False
            if c.execute("SELECT 1 FROM fenced WHERE service=? AND key=?", (service, key)).fetchone():
                c.execute("COMMIT")
                return None
            result, details = self.answer(service, key, user, payload)
            now = time.time()
            c.execute("INSERT INTO effects VALUES(?,?,?,?,?,?,?,?)",
                      (service, key, user, run, json.dumps(payload), result, json.dumps(details), now))
            c.execute("INSERT INTO applications(service,key,user,run,payload,at) VALUES(?,?,?,?,?,?)",
                      (service, key, user, run, json.dumps(payload), now))
            c.execute("COMMIT")
            return result, details, True
        except BaseException:
            c.execute("ROLLBACK")
            raise

    def call(self, action: str, key: str, user: str, run: str | None, token, payload: dict,
             attempt: int) -> tuple[str, dict]:
        service = SERVICES[action][0]
        m = self.mode(service, key, user, attempt)
        self.c.execute("INSERT INTO requests(service,key,user,attempt,mode,at) VALUES(?,?,?,?,?,?)",
                       (service, key, user, attempt, m, time.time()))
        lo, hi = self.cfg.get("latency_ms", {}).get(service, [1, 4])
        lo, hi = self.cfg.get("user_latency_ms", {}).get(user, [lo, hi])
        time.sleep((lo + h("lat", key, attempt) % (hi - lo + 1)) / 1000)
        if m == "error":
            raise ServerError(f"{service} 503")
        if m == "timeout_before":
            raise Timeout(f"{service} timeout (before acting)")
        if m == "delayed":
            self.c.execute("INSERT INTO delayed(service,key,user,action,token,run,payload,due) "
                           "VALUES(?,?,?,?,?,?,?,?)",
                           (service, key, user, action, token, run, json.dumps(payload),
                            time.time() + self.cfg.get("delay_s", 0.5)))
            raise Timeout(f"{service} timeout (request queued inside the system)")
        r = self._apply(service, key, user, run, payload)
        if r is None:
            raise ServerError(f"{service}: key fenced")
        result, details, fresh = r
        if m == "timeout_after":
            if fresh and token is not None and self.cfg.get("callbacks", True):
                # the system also reports the result later through its webhook
                self.c.execute("INSERT INTO callbacks(service,key,user,action,token,run,result,details,due,at) "
                               "VALUES(?,?,?,?,?,?,?,?,?,?)",
                               (service, key, user, action, token, run, result, json.dumps(details),
                                time.time() + self.cfg.get("user_callback_s", {}).get(user, self.cb_delay(key)),
                                time.time()))
            raise Timeout(f"{service} timeout (acted, answer lost)")
        return result, details

    def cb_delay(self, key: str) -> float:
        """Webhook deliveries are retried by the system: from callback_s up to callback_max_s."""
        lo, hi = self.cfg.get("callback_s", 0.3), self.cfg.get("callback_max_s", 0.3)
        return lo + (h("cb", key) % 1000) / 1000 * (hi - lo)

    def lookup(self, action: str, key: str):
        service = SERVICES[action][0]
        row = self.c.execute("SELECT result, details FROM effects WHERE service=? AND key=?",
                             (service, key)).fetchone()
        return (row[0], json.loads(row[1])) if row else None

    def fence(self, action: str, key: str):
        service = SERVICES[action][0]
        c = self.c
        c.execute("BEGIN IMMEDIATE")
        try:
            row = c.execute("SELECT result, details FROM effects WHERE service=? AND key=?",
                            (service, key)).fetchone()
            if not row:
                c.execute("INSERT OR IGNORE INTO fenced VALUES(?,?,?)", (service, key, time.time()))
            c.execute("COMMIT")
        except BaseException:
            c.execute("ROLLBACK")
            raise
        return (row[0], json.loads(row[1])) if row else None


def apply_delayed(path: str, ext: Ext | None = None) -> int:
    """The systems' own queue: apply requests whose time has come (driver side)."""
    ext = ext or Ext(path)
    rows = ext.c.execute("SELECT id, service, key, user, action, token, run, payload FROM delayed "
                         "WHERE done=0 AND due<=?", (time.time(),)).fetchall()
    for rid, service, key, user, action, token, run, payload in rows:
        r = ext._apply(service, key, user, run, json.loads(payload))
        ext.c.execute("UPDATE delayed SET done=1 WHERE id=?", (rid,))
        if r is not None and r[2] and ext.cfg.get("callbacks", True):
            ext.c.execute("INSERT INTO callbacks(service,key,user,action,token,run,result,details,due,at) "
                          "VALUES(?,?,?,?,?,?,?,?,?,?)",
                          (service, key, user, action, token, run, r[0], json.dumps(r[1]),
                           time.time() + ext.cb_delay(key), time.time()))
    return len(rows)

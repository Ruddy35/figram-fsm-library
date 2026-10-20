"""Minimal process transport for figram-engine (FORMAT.md §7). Written from scratch for this
host; it does NOT use ffi/python-example/figram_client.py.

* one request in flight per process (lock), responses read with a select() deadline;
* every response is validated strictly (id, mutually exclusive reply kinds, snapshot schema,
  action forms); any violation, EOF, EPIPE or timeout marks the process broken, kills it and
  raises EngineFailure — the caller retries (a step is a pure function, retry is safe until the
  host transaction commits);
* `{"id", "error", "code"}` replies are deterministic engine answers -> EngineReplyError with the
  machine code (not retried; the host distinguishes errors by `code`, never by the text —
  FORMAT.md §7, HOST.md §5). An error reply without a known `code` is a protocol violation.
"""
from __future__ import annotations

import json
import os
import select
import signal
import subprocess
import threading
import time
import zlib

MAX_INT = 9007199254740991


class EngineFailure(Exception):
    """Transport / protocol failure: retry with a fresh process."""


# FORMAT.md §7: every error reply is exactly {id, error, code}.
ERROR_CODES = {"parse_error", "too_large", "bad_request", "bad_snapshot", "seq_overflow"}


class EngineReplyError(Exception):
    """The engine answered {"id", "error", "code"}; `code` is the machine code."""

    def __init__(self, text: str, code: str):
        super().__init__(text)
        self.code = code


def _nat(x) -> bool:
    return type(x) is int and 0 <= x <= MAX_INT


def _name(x) -> bool:
    return type(x) is str and len(x) > 0


def valid_snapshot(s) -> str | None:
    """Strict check equivalent to snapshot.schema.json. Returns an error text or None."""
    if type(s) is not dict:
        return "snapshot is not an object"
    extra = set(s) - {"v", "at", "resume", "timers", "seq"}
    if extra:
        return f"unknown fields {sorted(extra)}"
    if "v" in s and not (type(s["v"]) is int and s["v"] == 1):
        return f"bad version {s['v']!r}"
    if not _name(s.get("at")):
        return "bad at"
    for k in ("resume", "timers"):
        if k in s and type(s[k]) is not dict:
            return f"{k} is not an object"
    for k, v in s.get("resume", {}).items():
        if not (_name(k) and _name(v)):
            return "bad resume entry"
    for k, v in s.get("timers", {}).items():
        if not (_name(k) and _nat(v)):
            return "bad timers entry"
    if "seq" in s and not _nat(s["seq"]):
        return "bad seq"
    return None


ACTION_KEYS = {"send": {"send"}, "call": {"call"}, "start_timer": {"start_timer", "ms", "token"},
               "cancel_timer": {"cancel_timer"}}


def check_reply(r, rid: int, op: str) -> str:
    if type(r) is not dict:
        raise EngineFailure(f"reply is not an object: {r!r:.200}")
    if "id" not in r:
        raise EngineFailure("reply without id")
    keys = set(r) - {"id"}
    if "error" in keys:
        if keys != {"error", "code"}:
            raise EngineFailure(f"bad error reply fields {sorted(keys)} (expected id, error, code)")
        if type(r["error"]) is not str:
            raise EngineFailure("error is not a string")
        if r["code"] not in ERROR_CODES:
            raise EngineFailure(f"unknown error code {r['code']!r}")
        # id null only when the engine could not read the request (one request in flight per
        # process, so the reply is still ours); otherwise the id must echo the request.
        if r["id"] is None and r["code"] in ("parse_error", "too_large"):
            return "error"
        if not (type(r["id"]) is int and r["id"] == rid):
            raise EngineFailure(f"reply id {r.get('id')!r} != {rid}")
        return "error"
    if not (type(r.get("id")) is int and r["id"] == rid):
        raise EngineFailure(f"reply id {r.get('id')!r} != {rid}")
    if op == "hello":
        if keys != {"protocol", "states"} or not _nat(r["protocol"]) or not _nat(r["states"]):
            raise EngineFailure(f"bad hello reply {r!r:.200}")
        return "hello"
    if op == "step" and keys == {"need"}:
        need = r["need"]
        if type(need) is not list or not need or len(set(map(str, need))) != len(need) or not all(map(_name, need)):
            raise EngineFailure(f"bad need {need!r:.200}")
        return "need"
    if keys not in ({"snapshot", "actions"}, {"snapshot", "actions", "recovered"}):
        raise EngineFailure(f"bad reply fields {sorted(keys)}")
    if "recovered" in r and r["recovered"] is not True:
        raise EngineFailure("bad recovered")
    err = valid_snapshot(r["snapshot"])
    if err:
        raise EngineFailure(f"engine returned invalid snapshot: {err}")
    if type(r["actions"]) is not list:
        raise EngineFailure("actions is not a list")
    for a in r["actions"]:
        if type(a) is not dict:
            raise EngineFailure("action is not an object")
        kinds = [k for k in ACTION_KEYS if k in a]
        if len(kinds) != 1 or set(a) != ACTION_KEYS[kinds[0]] or not _name(a[kinds[0]]):
            raise EngineFailure(f"bad action {a!r:.200}")
        if kinds[0] == "start_timer" and not (_nat(a["ms"]) and _nat(a["token"])):
            raise EngineFailure(f"bad start_timer {a!r:.200}")
    return "result"


class Faults:
    """Process-wide armed engine faults (set by the host's ctl handler)."""

    def __init__(self):
        self.lock = threading.Lock()
        self.armed: list[str] = []
        self.injected = 0
        self.detected = 0
        self.respawns = 0

    def arm(self, mode: str):
        with self.lock:
            self.armed.append(mode)

    def take(self) -> str | None:
        with self.lock:
            return self.armed.pop(0) if self.armed else None


class EngineProc:
    def __init__(self, argv: list[str], timeout: float, faults: Faults, proxied: bool):
        self.argv = argv
        self.timeout = timeout
        self.faults = faults
        self.proxied = proxied
        self.p = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL, bufsize=0)
        self.wfd = self.p.stdin.fileno()
        self.rfd = self.p.stdout.fileno()
        self.buf = b""
        self.next_id = 1
        self.lock = threading.Lock()
        self.broken: str | None = None

    def _kill(self):
        try:
            self.p.kill()
        except OSError:
            pass
        try:
            self.p.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass
        for f in (self.p.stdin, self.p.stdout):
            try:
                f.close()
            except OSError:
                pass

    def _break(self, why: str):
        if self.broken is None:
            self.broken = why
            self.faults.detected += 1
        self._kill()
        raise EngineFailure(why)

    def _write_all(self, data: bytes):
        view = memoryview(data)
        while view:
            n = os.write(self.wfd, view)
            view = view[n:]

    def _readline(self, deadline: float) -> bytes:
        while b"\n" not in self.buf:
            rem = deadline - time.monotonic()
            if rem <= 0:
                self._break(f"no reply within {self.timeout}s")
            r, _, _ = select.select([self.rfd], [], [], rem)
            if not r:
                continue
            chunk = os.read(self.rfd, 65536)
            if not chunk:
                self._break("engine closed its output (EOF)")
            self.buf += chunk
            if len(self.buf) > 16 * 1024 * 1024:
                self._break("reply line too long")
        line, _, self.buf = self.buf.partition(b"\n")
        return line

    def call(self, req: dict) -> dict:
        with self.lock:
            if self.broken:
                raise EngineFailure(self.broken)
            fault = self.faults.take()
            if fault == "kill":
                # real pipe break: the engine process dies before we write the request
                self.faults.injected += 1
                os.kill(self.p.pid, signal.SIGKILL)
                self.p.wait()
            elif fault is not None:
                if not self.proxied:
                    raise RuntimeError(f"engine fault {fault} needs --engine-proxy")
                self.faults.injected += 1
                self._write_all((json.dumps({"__evil__": fault}) + "\n").encode())
            rid = self.next_id
            self.next_id += 1
            body = json.dumps({"id": rid, **req}, ensure_ascii=False, separators=(",", ":"))
            try:
                self._write_all(body.encode() + b"\n")
            except (BrokenPipeError, OSError) as e:
                self._break(f"write failed: {e!r}")
            line = self._readline(time.monotonic() + self.timeout)
            try:
                r = json.loads(line)
            except ValueError as e:
                self._break(f"invalid JSON from engine: {e} in {line[:120]!r}")
            try:
                kind = check_reply(r, rid, req["op"])
            except EngineFailure as e:
                self._break(str(e))
            if kind == "error":
                raise EngineReplyError(r["error"], r["code"])
            return r

    def close(self):
        try:
            self.p.stdin.close()
        except OSError:
            pass
        try:
            self.p.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self._kill()


class EnginePool:
    """K engine processes for ONE bot document; a user always maps to the same process."""

    def __init__(self, engine_bin: str, doc_path: str, k: int, timeout: float, faults: Faults,
                 proxy: str | None = None):
        self.argv = ([os.environ.get("PYTHON", "python3"), "-I", proxy, engine_bin, doc_path]
                     if proxy else [engine_bin, doc_path])
        self.proxied = proxy is not None
        self.timeout = timeout
        self.faults = faults
        self.lock = threading.Lock()
        self.procs = [self._spawn() for _ in range(k)]
        r = self.procs[0].call({"op": "hello"})
        self.states = r["states"]

    def _spawn(self) -> EngineProc:
        return EngineProc(self.argv, self.timeout, self.faults, self.proxied)

    def call(self, user: str, req: dict) -> dict:
        i = zlib.crc32(user.encode()) % len(self.procs)
        with self.lock:
            p = self.procs[i]
            if p.broken:
                p = self.procs[i] = self._spawn()
                self.faults.respawns += 1
        return p.call(req)

    def close(self):
        for p in self.procs:
            p.close()

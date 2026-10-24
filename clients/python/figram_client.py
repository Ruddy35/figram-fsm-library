"""Python client of figram-engine: a pool of processes talking over stdin/stdout.

Each engine process is single-threaded, handles requests one at a time and
replies strictly in request order (docs/format.md, section 8). The client sends
requests without waiting for the replies to earlier ones (pipelining): every
request has its own `id`, and a separate thread reads the replies and wakes the
waiting caller. Parallelism comes from several processes (one per core).

A user always goes to the same process (by a hash of the key); this only
spreads the load. The client does NOT keep a user's events in order or protect
the integrity of the user's snapshot: the engine is stateless, so two events of
one user sent without a lock are computed from the same snapshot and one of
them is lost; when a process fails, all its requests fail, and the host's
retries may arrive in any order. The host processes the events of a user one at
a time, under a per-user lock (docs/host-contract.md, section 1, step 1).

Process life cycle (docs/host-contract.md, section 5):

* Start. The process loads the document (seconds or minutes for large
  documents); the client sends `hello` right away and waits for the reply at
  most `start_timeout`. The process accepts requests only after the reply;
  calls made during loading wait until it is ready, and their `timeout` counts
  from then. Request timeouts do not kill a process that is still loading.
* A request timeout fails only that call: its `id` is marked abandoned, the
  late reply is discarded, and the process keeps running. A process counts as
  hung only if it prints nothing for `hang_timeout` seconds while requests are
  unanswered.
* A protocol violation (invalid JSON or UTF-8, a reply line longer than
  `max_line`, a foreign or non-integer id, a reply out of order, a reply of the
  wrong shape, closed output, a crash, no reply to `hello`) and a hang mark the
  process as failed, wake every waiting call with an error and kill the
  process. The exception is an error reply with `"id": null` (the engine could
  not parse the request line): replies come in order, so it fails only the
  oldest unanswered request.
* Replacement. The next call to a failed process starts a new one and waits
  for it to load. If the processes of a slot keep failing before serving a
  single request (for example, the document does not load), restarts are
  delayed with an exponential `restart_backoff`; during the delay calls fail
  at once with an `EngineError` carrying the reason of the last failure,
  including the tail of the engine's stderr.
"""

from __future__ import annotations

import collections
import itertools
import json
import os
import queue
import re
import select
import signal
import subprocess
import threading
import time
import zlib
from typing import Callable, Iterable

from protocol import ProtocolError, check_reply


class EngineError(Exception):
    """A failed engine call.

    * `retryable` — True: a failure of the process or of the transport (no reply
      in time, the process crashed, violated the protocol, did not load, is
      restarting): a step is a pure function, so the host repeats it
      (docs/host-contract.md, section 5). False — the failure is deterministic:
      an engine reply with `error` (snapshot rejected, `seq_overflow`, line not
      parsed) or an argument that cannot be sent; a retry gives the same result.
    * `code` — the machine-readable code from the engine reply (`parse_error`,
      `too_large`, `bad_request`, `bad_snapshot`, `seq_overflow`); None if there
      was no error reply or the reply has no `code` field (pre-release engines).
    """

    def __init__(self, message: str, *, code: str | None = None, retryable: bool = True) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


PROTOCOL = 2
# FormatIO.maxDepth: the maximum nesting of arrays and objects in a request line.
MAX_DEPTH = 256
# Default maximum length of a reply line; a longer line is a protocol violation.
MAX_LINE = 16 * 1024 * 1024
STDERR_TAIL = 2048        # how many last bytes of the engine's stderr go into errors
_TICK = 0.25              # how often the reader thread checks the deadlines
_DRAIN = 5.0              # after a failure, output is read until EOF for at most this long
_HELLO_ID = 0             # id of the start-up hello; calls use 1 and up
_CLOSED = "client closed"
_MISSING = object()
# An escaped surrogate in the reply text (\ud800…\udfff): only then is the
# element-wise check for lone surrogates needed.
_ESC_SURROGATE = re.compile(r"\\u[dD][89a-fA-F]")


def _check_wire(x, depth: int = 0) -> None:
    """A request that the engine can parse at the text level.

    A line that cannot be parsed (a duplicate key, nesting deeper than
    MAX_DEPTH, a lone surrogate) is rejected by the engine with an error reply,
    by pre-release engines even without the id. Such requests are rejected here,
    before sending: keys must be strings (json.dumps turns 1 and "1", True and
    "true" into equal keys) and nesting is limited; encoding rejects surrogates.
    """
    if isinstance(x, dict):
        if depth >= MAX_DEPTH:
            raise ValueError(f"nesting deeper than {MAX_DEPTH}")
        for k, v in x.items():
            if not isinstance(k, str):
                raise TypeError(f"key {k!r} is not a string")
            _check_wire(v, depth + 1)
    elif isinstance(x, (list, tuple)):
        if depth >= MAX_DEPTH:
            raise ValueError(f"nesting deeper than {MAX_DEPTH}")
        for v in x:
            _check_wire(v, depth + 1)


def _decode(raw: bytes):
    """Reply line → JSON value, strictly: UTF-8 only and no lone surrogates,
    neither encoded as bytes (CESU) nor escaped."""
    text = raw.decode("utf-8")          # UnicodeDecodeError ⊂ ValueError
    value = json.loads(text)
    if _ESC_SURROGATE.search(text):
        # json joins a surrogate pair into one character; a lone one does not encode.
        json.dumps(value, ensure_ascii=False).encode("utf-8")
    return value


def _error_reply(resp: dict) -> bool:
    """An error reply: exactly {id, error} (pre-release engines) or {id, error, code}."""
    if "error" not in resp:
        return False
    keys = resp.keys() - {"id"}
    if keys not in ({"error"}, {"error", "code"}):
        raise ProtocolError(f"reply with error and extra fields: {sorted(keys)}")
    if type(resp["error"]) is not str:
        raise ProtocolError("error is not a string")
    if "code" in resp and (type(resp["code"]) is not str or not resp["code"]):
        raise ProtocolError(f"error code is not a non-empty string: {resp['code']!r}")
    return True


def _check_hello(resp: dict) -> None:
    keys = resp.keys() - {"id"}
    # "mutant" — only from the corrupted test engine (FIGRAM_ALLOW_MUTANTS).
    if not {"protocol", "states"} <= keys <= {"protocol", "states", "mutant"}:
        raise ProtocolError(f"hello reply: fields {sorted(keys)}")
    if type(resp["protocol"]) is not int or type(resp["states"]) is not int:
        raise ProtocolError(f"hello reply: {resp!r}")
    if "mutant" in resp and type(resp["mutant"]) is not str:
        raise ProtocolError(f"hello reply: mutant {resp['mutant']!r}")
    if resp["protocol"] != PROTOCOL:
        raise ProtocolError(f"engine protocol {resp['protocol']}, the client supports only {PROTOCOL}")


class _Config:
    __slots__ = ("timeout", "start_timeout", "hang_timeout", "max_line")

    def __init__(self, timeout, start_timeout, hang_timeout, max_line) -> None:
        self.timeout = timeout
        self.start_timeout = start_timeout
        self.hang_timeout = hang_timeout
        self.max_line = max_line


class _Call:
    """A request in flight. kind: "user" — a call of the host; "start" — the
    start-up hello; "probe" — a liveness hello after a timeout."""
    __slots__ = ("done", "response", "op", "kind", "sent", "abandoned")

    def __init__(self, op: str, kind: str = "user") -> None:
        self.done = threading.Event()
        self.response = None
        self.op = op
        self.kind = kind
        self.sent = False          # written to stdin: a reply to it is required
        self.abandoned = False     # the call timed out: discard the late reply


class _Process:
    """One engine process.

    * A separate writer thread writes to stdin; the calling thread never blocks
      on I/O and waits for the reply at most `timeout` from the start of the
      call (from readiness if the process is still loading), including the time
      the request waits in the write queue.
    * The reader thread reads stdout and stderr (the tail goes into error
      texts), watches the start-up and hang deadlines and limits the length of
      a reply line.
    * The lock protects only the request tables and no I/O is done under it,
      so an emergency stop never waits for it.
    """

    def __init__(self, binary: str, document: str, cfg: _Config) -> None:
        self._p = subprocess.Popen(
            [binary, document],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        self._cfg = cfg
        self._timeout = cfg.timeout
        self.spawned_at = time.monotonic()
        self._lock = threading.Lock()
        self._ids = itertools.count(1)
        self._pending: dict[int, _Call] = {}     # id → request without a reply
        self._sent: collections.deque = collections.deque()   # ids of written requests, in order
        self._outbox: queue.SimpleQueue = queue.SimpleQueue()
        self.failure: str | None = None
        self.failed_at: float | None = None
        self.ready_at: float | None = None
        self.served = False                      # answered at least one call
        self._started = threading.Event()        # loading is over: ready or failed
        self._last_progress = self.spawned_at
        self._probe: int | None = None
        self._exit: str | None = None
        self._err = bytearray()
        self._closing = False
        self._pending[_HELLO_ID] = _Call("hello", "start")
        self._outbox.put((_HELLO_ID, b'{"id":%d,"op":"hello"}\n' % _HELLO_ID))
        self._writer = threading.Thread(target=self._write_loop, daemon=True)
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._writer.start()
        self._reader.start()

    @property
    def alive(self) -> bool:
        return self.failure is None

    # --- failure and shutdown ---------------------------------------------

    def _peek_exit(self) -> None:
        """Record the exit status if the process has already exited by itself.
        Without reaping it (WNOWAIT): the pid stays ours until wait()."""
        if self._exit is not None:
            return
        try:
            st = os.waitid(os.P_PID, self._p.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
        except (AttributeError, OSError):
            return
        if st is None:
            return
        if st.si_code == os.CLD_EXITED:
            self._exit = f"engine exited with code {st.si_status}"
        elif st.si_code in (os.CLD_KILLED, os.CLD_DUMPED):
            self._exit = f"engine killed by signal {st.si_status}"

    def _kill(self) -> None:
        self._peek_exit()
        try:
            os.killpg(self._p.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        # Reap the process right away without blocking the calling thread (no zombies).
        threading.Thread(target=self._p.wait, daemon=True).start()

    def _fail(self, reason: str, kill: bool = True) -> bool:
        """Mark the process failed, wake all waiting calls, kill the process.

        Only the first call kills: later ones (the reader sees EOF, the writer
        EPIPE) come when the background wait() may already have reaped the
        process and the pid is free, so a SIGKILL to it could hit another
        process group. Returns True if this call was the first."""
        with self._lock:
            first = self.failure is None
            if first:
                self.failure = reason
                self.failed_at = time.monotonic()
            pending, self._pending = self._pending, {}
            self._sent.clear()
        for c in pending.values():
            c.done.set()
        if first:
            self._started.set()
            self._outbox.put(None)
            if kill:
                self._kill()
        return first

    def stderr_tail(self) -> str:
        text = bytes(self._err[-STDERR_TAIL:]).decode("utf-8", "replace")
        return " | ".join(s.strip() for s in text.splitlines() if s.strip())

    def error_text(self) -> str:
        """The failure reason with the exit status and the tail of the engine's stderr."""
        if self.failure != _CLOSED and threading.current_thread() is not self._reader:
            self._reader.join(0.25)      # read the rest of the failed process's stderr
        text = self.failure or "unknown failure"
        if self._exit:
            text += f"; {self._exit}"
        tail = self.stderr_tail()
        if tail:
            text += f"; stderr: {tail}"
        return text

    def _failed_error(self) -> EngineError:
        return EngineError(f"engine process failed: {self.error_text()}",
                           retryable=self.failure != _CLOSED)

    # --- calls ------------------------------------------------------------

    def wait_ready(self) -> None:
        """Wait until loading is over (the reply to hello); EngineError if the
        process did not start. The reader thread enforces the start-up deadline."""
        if self.ready_at is None and self.failure is None:
            t = self._cfg.start_timeout
            self._started.wait(None if t is None else
                               max(0.0, self.spawned_at + t - time.monotonic()) + 1.0)
        if self.failure is not None:
            raise self._failed_error()
        if self.ready_at is None:
            self._fail(f"engine did not answer hello within {self._cfg.start_timeout} s")
            raise self._failed_error()

    def call(self, request: dict, deadline: float | None = None) -> dict:
        """Send a request and wait for the reply: at most `timeout` from the start
        of the call or from the process becoming ready, and no later than
        `deadline` (monotonic)."""
        # Serialize and encode before registering: an invalid argument fails the
        # call and leaves the pending table and the process untouched.
        # UnicodeEncodeError (a lone surrogate) is a subclass of ValueError.
        try:
            _check_wire(request)
            body = json.dumps(request, ensure_ascii=False, separators=(",", ":"),
                              allow_nan=False).encode()
        except (TypeError, ValueError, RecursionError) as e:
            raise EngineError(f"request is not JSON-serializable: {e}", retryable=False) from None
        self.wait_ready()
        limit = self._timeout
        end = time.monotonic() + limit
        if deadline is not None and deadline < end:
            end, limit = deadline, max(0.0, deadline - time.monotonic())
        c = _Call(request["op"])
        with self._lock:
            failed = self.failure is not None
            if not failed:
                rid = next(self._ids)
                self._pending[rid] = c
        if failed:
            raise self._failed_error()
        # the id is prepended to the already serialized object: {"id":N,...}
        line = b'{"id":' + str(rid).encode() + (b"," + body[1:] if body != b"{}" else b"}")
        self._outbox.put((rid, line + b"\n"))
        if not c.done.wait(max(0.0, end - time.monotonic())):
            with self._lock:
                mine = c.response is None and self._pending.get(rid) is c
                if mine:
                    if c.sent:
                        c.abandoned = True       # the reply will come: discard it
                    else:
                        del self._pending[rid]   # not written: the writer skips it
            if mine:
                self._probe_health()
                raise EngineError(f"no reply within {limit:.3g} s")
        if c.response is None:
            raise self._failed_error()
        return c.response

    def _probe_health(self) -> None:
        """After a timeout, queue a hello (at most one at a time): its reply
        confirms that the process is alive and replies in order."""
        with self._lock:
            if self.failure is not None or self._probe is not None:
                return
            rid = next(self._ids)
            self._probe = rid
            self._pending[rid] = _Call("hello", "probe")
        self._outbox.put((rid, b'{"id":%d,"op":"hello"}\n' % rid))

    # --- threads ----------------------------------------------------------

    def _write_loop(self) -> None:
        stdin = self._p.stdin
        try:
            while True:
                item = self._outbox.get()
                if item is None:
                    return
                rid, line = item
                with self._lock:
                    c = self._pending.get(rid)
                    if c is None:            # removed before writing: timeout, failure, close
                        continue
                    if not self._sent:
                        self._last_progress = time.monotonic()
                    self._sent.append(rid)
                    c.sent = True
                try:
                    stdin.write(line)
                    stdin.flush()
                except (OSError, ValueError) as e:
                    self._fail(f"write failed: {e!r}")
                    return
        finally:
            # Only the writer closes stdin. The rest of the buffer cannot be sent
            # to a dead process: the broken pipe error is suppressed here, or the
            # finalizer would print it.
            try:
                stdin.close()
            except (OSError, ValueError):
                pass

    def _check_deadlines(self) -> float:
        """The start-up deadline (before readiness) and the hang deadline (after);
        return how long to wait for output until the next check."""
        now = time.monotonic()
        cfg = self._cfg
        if self.ready_at is None:
            if cfg.start_timeout is None:
                return _TICK
            left = self.spawned_at + cfg.start_timeout - now
            if left <= 0:
                self._fail(f"engine did not answer hello within {cfg.start_timeout} s (loading the document)")
            return max(0.0, min(_TICK, left))
        with self._lock:
            busy, last = bool(self._sent), self._last_progress
        if not busy:
            return _TICK
        left = last + cfg.hang_timeout - now
        if left <= 0:
            self._fail(f"engine hung: no output for {cfg.hang_timeout} s with unanswered requests")
        return max(0.0, min(_TICK, left))

    def _read_loop(self) -> None:
        out, err = self._p.stdout, self._p.stderr
        ofd, efd = out.fileno(), err.fileno()
        poller = select.poll()
        for fd in (ofd, efd):
            poller.register(fd, select.POLLIN)
        open_fds = {ofd, efd}
        frags: list[bytes] = []          # the start of a reply line without its end
        flen = 0
        drain_until = None
        max_line = self._cfg.max_line
        try:
            while open_fds:
                if self.failure is None:
                    wait = self._check_deadlines()
                    if self.failure is not None:
                        continue
                else:
                    # After a failure: read the output until EOF (the stderr tail; a
                    # process that still writes replies at close does not block).
                    if drain_until is None:
                        drain_until = time.monotonic() + _DRAIN
                    wait = drain_until - time.monotonic()
                    if wait <= 0:
                        break
                for fd, _ in poller.poll(wait * 1000):
                    if fd not in open_fds:
                        continue
                    chunk = os.read(fd, 1 << 16)
                    if fd == efd:
                        if chunk:
                            self._add_stderr(chunk)
                        else:
                            poller.unregister(efd)
                            open_fds.discard(efd)
                        continue
                    if not chunk:
                        poller.unregister(ofd)
                        open_fds.discard(ofd)
                        if self.failure is None:
                            # A last line without a newline is accepted.
                            if frags and not self._on_line(b"".join(frags)):
                                continue
                            self._on_eof(poller, efd, open_fds)
                        continue
                    if self.failure is not None:
                        continue
                    self._last_progress = time.monotonic()
                    start = 0
                    while True:
                        nl = chunk.find(b"\n", start)
                        if nl < 0:
                            break
                        if frags:
                            frags.append(chunk[start:nl])
                            line = b"".join(frags)
                            frags, flen = [], 0
                        else:
                            line = chunk[start:nl]
                        start = nl + 1
                        if max_line and len(line) > max_line:
                            self._fail(f"reply line longer than {max_line} bytes")
                            break
                        if not self._on_line(line):
                            break
                    if self.failure is None and start < len(chunk):
                        frags.append(chunk[start:])
                        flen += len(chunk) - start
                        if max_line and flen > max_line:
                            self._fail(f"reply line longer than {max_line} bytes (no end of line)")
                    if self.failure is not None:
                        frags, flen = [], 0
        except Exception as e:  # noqa: BLE001 — any read failure
            self._fail(f"read failed: {e!r}")
        finally:
            for f in (out, err):
                try:
                    f.close()
                except OSError:
                    pass

    def _add_stderr(self, chunk: bytes) -> None:
        self._err += chunk
        if len(self._err) > 2 * STDERR_TAIL:
            del self._err[:-STDERR_TAIL]

    def _on_eof(self, poller, efd: int, open_fds: set) -> None:
        """stdout is closed: briefly read the rest of stderr (it holds the reason,
        for example a document error) and the exit status, then fail."""
        end = time.monotonic() + 0.2
        while efd in open_fds:
            left = end - time.monotonic()
            if left <= 0 or not poller.poll(left * 1000):
                break
            chunk = os.read(efd, 1 << 16)
            if not chunk:
                poller.unregister(efd)
                open_fds.discard(efd)
                break
            self._add_stderr(chunk)
        for _ in range(5):          # stderr is closed: the process is about to become a zombie
            self._peek_exit()
            if self._exit is not None or efd in open_fds:
                break
            time.sleep(0.01)
        self._fail("engine closed its output")

    def _on_line(self, raw: bytes) -> bool:
        """Parse a reply line; False — the process has been marked failed."""
        try:
            resp = _decode(raw)
        except (ValueError, RecursionError) as e:
            self._fail(f"invalid reply (JSON or UTF-8): {e!r} in {raw[:200]!r}")
            return False
        if type(resp) is not dict:
            self._fail(f"reply is not an object: {raw[:200]!r}")
            return False
        try:
            is_error = _error_reply(resp)
        except ProtocolError as e:
            self._fail(f"protocol violation: {e}")
            return False
        rid = resp.get("id", _MISSING)
        with self._lock:
            if self.failure is not None:
                return False
            head = self._sent[0] if self._sent else None
            if rid is None and is_error and head is not None:
                # The line was not parsed and its id is unknown: replies come
                # strictly in request order, so this answers the oldest request.
                rid = head
            c = self._pending.get(rid) if type(rid) is int else None
        if type(rid) is not int:
            self._fail(f"reply without an integer id: {raw[:200]!r}")
            return False
        if c is None:
            self._fail(f"reply with an unknown id {rid}")
            return False
        if rid != head:
            self._fail(f"reply to request {rid} before the reply to {head}: replies out of order")
            return False
        try:
            if is_error:
                pass
            elif c.op == "hello":
                _check_hello(resp)
            else:
                check_reply(resp, rid, c.op)
        except ProtocolError as e:
            self._fail(f"protocol violation: {e}")
            return False
        with self._lock:
            if self._pending.get(rid) is not c:      # a failure between the checks
                return False
            del self._pending[rid]
            self._sent.popleft()
            if c.kind == "user":
                self.served = True
                if not c.abandoned:
                    c.response = resp
            elif c.kind == "probe":
                self._probe = None
        if c.kind == "user":
            c.done.set()
        elif is_error:
            self._fail(f"engine rejected hello: {resp['error']}")
            return False
        elif c.kind == "start":
            self.ready_at = time.monotonic()
            self._started.set()
        return True

    def _begin_close(self) -> None:
        # New calls fail and waiting calls are woken at once. A ready process is
        # not killed: the writer closes stdin and the engine exits on EOF. A
        # process that is still loading is killed at once: it would read its
        # input only after loading, and it has no requests in progress.
        loading = self.ready_at is None
        first = self._fail(_CLOSED, kill=loading)
        self._closing = first and not loading     # alive, not killed: close stops it

    def _finish_close(self, deadline: float) -> None:
        self._writer.join(max(0.0, deadline - time.monotonic()))
        try:
            self._p.wait(timeout=max(0.0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            if self._closing:
                self._kill()   # alive and not reaped: the pid is still ours
            self._p.wait()

    def close(self, deadline: float | None = None) -> None:
        """Stop the process within `timeout` (or by `deadline`), even if the
        engine hangs."""
        self._begin_close()
        self._finish_close(time.monotonic() + self._timeout if deadline is None else deadline)


class Engine:
    """A pool of `figram-engine` processes. Safe to call from many threads.

    * `timeout` — the deadline of one engine request: from the start of the call
      or from the process becoming ready if it is still loading;
    * `start_timeout` — the deadline for loading the document (the reply to
      hello), per process; None — no limit;
    * `hang_timeout` — a process that prints nothing for this many seconds while
      requests are unanswered counts as hung and is replaced; None — the
      default max(60, 3 × timeout); math.inf — never;
    * `step_timeout` — the overall deadline of `step` and of `start` with an event
      from the process becoming ready: both engine requests (asking for the
      conditions and repeating with them) and the conditions function;
      None — 2 × timeout;
    * `max_line` — the maximum length of a reply line in bytes (0 — no limit);
    * `restart_backoff` — (initial, maximum) delay in seconds before restarting
      a slot again whose processes fail without serving a single request; it
      doubles with every such failure and resets when a process of the slot has
      answered a request; (0, 0) — no delays;
    * `wait_ready` — the constructor waits until all processes have loaded and
      raises EngineError (with the tail of the engine's stderr) if one did not
      start; False — return at once, calls wait for readiness themselves.
    """

    def __init__(self, binary: str, document: str, processes: int = 1,
                 timeout: float = 10.0, *, start_timeout: float | None = 600.0,
                 hang_timeout: float | None = None, step_timeout: float | None = None,
                 max_line: int = MAX_LINE, restart_backoff: tuple[float, float] = (0.1, 30.0),
                 wait_ready: bool = True) -> None:
        if processes < 1:
            raise ValueError("processes must be at least 1")
        if hang_timeout is None:
            hang_timeout = max(60.0, 3 * timeout)
        self._cfg = _Config(timeout, start_timeout, hang_timeout, max_line)
        self._step_timeout = 2 * timeout if step_timeout is None else step_timeout
        self._backoff = restart_backoff
        self._args = (binary, document)
        self._swap = threading.Lock()
        self._closed = False
        self._procs: list[_Process] = []
        self._fails = [0] * processes                # processes of the slot that failed in a row
        self._retry_at = [0.0] * processes           # do not restart the slot before this time
        self._seen: list[_Process | None] = [None] * processes
        try:
            for _ in range(processes):
                self._procs.append(_Process(binary, document, self._cfg))
        except BaseException:
            self.close()
            raise
        if wait_ready:
            for p in self._procs:
                try:
                    p.wait_ready()
                except EngineError as e:
                    self.close()
                    raise EngineError(f"engine did not start: {e}") from None

    def _delay(self, fails: int) -> float:
        base, cap = self._backoff
        if fails <= 0 or base <= 0:
            return 0.0
        return min(cap, base * 2 ** min(fails - 1, 60))

    def _proc(self, key: str) -> _Process:
        """The user's process; a failed one is replaced by a new one (the requests
        in progress on the old one have already failed; the host repeats them as
        the contract requires)."""
        if self._closed:
            raise EngineError(_CLOSED, retryable=False)
        i = zlib.crc32(key.encode()) % len(self._procs)
        p = self._procs[i]
        if p.alive:
            return p
        wait = spawn_error = None
        with self._swap:
            if self._closed:
                raise EngineError(_CLOSED, retryable=False)
            p = self._procs[i]
            if p.alive:
                return p
            now = time.monotonic()
            if self._seen[i] is not p:       # this failure is not counted yet: schedule the delay
                self._seen[i] = p
                if p.served:
                    self._fails[i] = 0
                self._retry_at[i] = (p.failed_at or now) + self._delay(self._fails[i])
                self._fails[i] += 1
            if now < self._retry_at[i]:
                wait = self._retry_at[i] - now
            else:
                try:
                    self._procs[i] = _Process(*self._args, self._cfg)
                except OSError as e:   # no binary, no permission, EMFILE, EAGAIN
                    self._retry_at[i] = now + self._delay(self._fails[i])
                    self._fails[i] += 1
                    spawn_error = e
                else:
                    return self._procs[i]
        if spawn_error is not None:
            raise EngineError(f"cannot start the engine: {spawn_error}") from None
        raise EngineError(f"engine process restarts in {wait:.1f} s; "
                          f"last failure: {p.error_text()}")

    def start(
        self,
        key: str,
        seq: int | None = None,
        event: str | None = None,
        guards: Callable[[Iterable[str]], dict[str, bool]] | None = None,
    ) -> dict:
        """A new user. `seq` — the user's high-water mark of `seq` to continue the
        counter from (docs/host-contract.md, section 4). `event` — the user's first
        event (for example "cmd:start" or a button of an old message): it is matched
        as if the user stood on the initial message (docs/format.md, section 8);
        `guards` computes the conditions it needs, as in `step`."""
        request = {"op": "start"}
        if seq is not None:
            request["seq"] = seq
        if event is None:
            if guards is not None:
                raise ValueError("guards are used only with an event")
            return self._check(self._proc(key).call(request))
        request["event"] = event
        return self._conditional(self._proc(key), request, guards)

    def restart(self, key: str, snapshot: dict) -> dict:
        """Start the user over without resetting the token counter
        (docs/host-contract.md, section 4)."""
        return self._check(self._proc(key).call({"op": "restart", "snapshot": snapshot}))

    def step(
        self,
        key: str,
        snapshot: dict,
        event: str,
        guards: Callable[[Iterable[str]], dict[str, bool]] | None = None,
        token: int | None = None,
    ) -> dict:
        """A machine step. `guards(names)` computes the developer's conditions;
        it is called only if the engine says they are needed for this step.
        For a timer event `timer:<name>` pass the `token` of its start_timer.
        The whole step fits in `step_timeout` from the process becoming ready;
        the conditions function cannot be interrupted, but if it returns after
        the deadline, the second request is not sent and EngineError is raised."""
        request = {"op": "step", "snapshot": snapshot, "event": event}
        if token is not None:
            request["token"] = token
        return self._conditional(self._proc(key), request, guards)

    def _conditional(self, proc, request: dict, guards) -> dict:
        """A request that may answer `need` (`step`, `start` with an event): the
        conditions are computed with `guards` and the request is repeated once."""
        proc.wait_ready()
        deadline = time.monotonic() + self._step_timeout
        response = proc.call(request, deadline)
        if "need" in response:
            need = response["need"]
            if guards is None:
                raise EngineError(f"conditions needed: {need}", retryable=False)
            values = guards(list(need))
            if type(values) is not dict:
                raise EngineError(f"the conditions function returned {type(values).__name__}, not a dict",
                                  retryable=False)
            missing = [n for n in need if n not in values]
            wrong = [n for n in need if n in values and type(values[n]) is not bool]
            if missing or wrong:
                raise EngineError(f"the conditions function: no values for {missing}, not bool: {wrong}",
                                  retryable=False)
            if time.monotonic() >= deadline:
                raise EngineError(f"the step did not fit in {self._step_timeout:.3g} s: "
                                  "the conditions function returned after the deadline")
            response = proc.call({**request, "guards": {n: values[n] for n in need}}, deadline)
            if "need" in response:
                raise EngineError(f"the engine asked for conditions again: {response['need']}")
        response = self._check(response)
        if "snapshot" not in response:
            raise EngineError(f"incomplete {request['op']} reply: {response}")
        return response

    @staticmethod
    def _check(response: dict) -> dict:
        if "error" in response:
            raise EngineError(response["error"], code=response.get("code"), retryable=False)
        return response

    def close(self) -> None:
        """Close the pool: afterwards calls raise EngineError and no processes are
        started. The processes are closed at the same time, with one shared
        `timeout` deadline."""
        with self._swap:
            self._closed = True
            procs = list(self._procs)
        for p in procs:
            p._begin_close()
        deadline = time.monotonic() + self._cfg.timeout
        for p in procs:
            p._finish_close(deadline)

"""Strict checks of engine protocol replies (docs/format.md, section 8) and running
child processes with a hard deadline. Used by the client and by all tests.

Rule: a reply is exactly one of the variants "result", "need", "error"; field
types are checked exactly (a boolean is not a number). An error reply has
exactly the fields `id`, `error` (a text for humans) and `code` (a machine code
from ERROR_CODES, docs/format.md, section 8, "Errors").
"""

from __future__ import annotations

import os
import signal
import subprocess

MAX_INT = 9007199254740991

# Machine codes of an error reply (docs/format.md, section 8, "Errors").
ERROR_CODES = frozenset({"parse_error", "too_large", "bad_request", "bad_snapshot", "seq_overflow"})


class ProtocolError(Exception):
    """An engine reply violates the protocol."""


class ChildTimeout(Exception):
    """A child process did not finish in time."""


def is_int(x) -> bool:
    return type(x) is int


def nat(x, what: str) -> None:
    if not is_int(x) or not 0 <= x <= MAX_INT:
        raise ProtocolError(f"{what}: expected an integer 0…2^53−1, got {x!r}")


def name(x, what: str) -> None:
    if type(x) is not str or not x:
        raise ProtocolError(f"{what}: expected a non-empty string, got {x!r}")


def check_snapshot(s) -> None:
    """Strictly by schemas/snapshot.schema.json."""
    if type(s) is not dict:
        raise ProtocolError(f"snapshot is not an object: {s!r}")
    extra = set(s) - {"v", "at", "resume", "timers", "seq"}
    if extra:
        raise ProtocolError(f"snapshot: extra fields {sorted(extra)}")
    if "v" in s and (not is_int(s["v"]) or s["v"] != 1):
        raise ProtocolError(f"snapshot: version {s['v']!r}")
    name(s.get("at"), "snapshot.at")
    for k, field in (("resume", "resume"), ("timers", "timers")):
        if k in s and type(s[k]) is not dict:
            raise ProtocolError(f"snapshot.{field} is not an object")
    for k, v in s.get("resume", {}).items():
        name(k, "snapshot.resume: section")
        name(v, f"snapshot.resume.{k}")
    for k, v in s.get("timers", {}).items():
        name(k, "snapshot.timers: name")
        nat(v, f"snapshot.timers.{k}")
    if "seq" in s:
        nat(s["seq"], "snapshot.seq")


ACTION_FORMS = {
    "send": {"send"},
    "call": {"call"},
    "start_timer": {"start_timer", "ms", "token"},
    "cancel_timer": {"cancel_timer"},
}


def check_actions(acts) -> None:
    if type(acts) is not list:
        raise ProtocolError(f"actions is not a list: {acts!r}")
    for a in acts:
        if type(a) is not dict:
            raise ProtocolError(f"action is not an object: {a!r}")
        kinds = [k for k in ACTION_FORMS if k in a]
        if len(kinds) != 1 or set(a) != ACTION_FORMS[kinds[0]]:
            raise ProtocolError(f"unknown action form: {a!r}")
        name(a[kinds[0]], f"action {kinds[0]}")
        if kinds[0] == "start_timer":
            nat(a["ms"], "start_timer.ms")
            nat(a["token"], "start_timer.token")


def check_error(resp) -> str:
    """Check the form of an error reply (exactly `id`, `error`, `code`; `id` is
    any JSON value or null); return the code."""
    if type(resp) is not dict or set(resp) != {"id", "error", "code"}:
        raise ProtocolError(f"error reply: fields {sorted(resp) if type(resp) is dict else resp!r}")
    if type(resp["error"]) is not str or not resp["error"]:
        raise ProtocolError(f"error is not a non-empty string: {resp['error']!r}")
    if resp["code"] not in ERROR_CODES:
        raise ProtocolError(f"unknown error code {resp['code']!r}")
    return resp["code"]


def check_reply(resp, rid: int, op: str) -> str:
    """Check the reply to request `op` with id `rid`; return the variant:
    'result', 'need', 'error' or 'hello'."""
    if type(resp) is not dict:
        raise ProtocolError(f"reply is not an object: {resp!r}")
    if not is_int(resp.get("id")) or resp["id"] != rid:
        raise ProtocolError(f"reply id {resp.get('id')!r} instead of {rid}")
    keys = set(resp) - {"id"}
    if "error" in keys:
        check_error(resp)
        return "error"
    if op == "hello":
        # "mutant" — only from a corrupted engine in test mode (FIGRAM_ALLOW_MUTANTS=1)
        if keys - {"mutant"} != {"protocol", "states"} or not is_int(resp["protocol"]) \
                or not is_int(resp["states"]) or ("mutant" in resp and type(resp["mutant"]) is not str):
            raise ProtocolError(f"hello reply: {resp!r}")
        return "hello"
    if op == "step" and keys == {"need"}:
        need = resp["need"]
        if type(need) is not list or not need or len(set(need)) != len(need):
            raise ProtocolError(f"need: {need!r}")
        for n in need:
            name(n, "need")
        return "need"
    if keys not in ({"snapshot", "actions"}, {"snapshot", "actions", "recovered"}):
        raise ProtocolError(f"{op} reply: fields {sorted(keys)}")
    if "recovered" in resp and resp["recovered"] is not True:
        raise ProtocolError(f"recovered: {resp['recovered']!r}")
    check_snapshot(resp["snapshot"])
    check_actions(resp["actions"])
    return "result"


def run_child(cmd, data: bytes, timeout: float, phase: str, env=None) -> subprocess.CompletedProcess:
    """Run a process with the given input; when the deadline passes, kill it
    together with its descendants and raise ChildTimeout naming the phase."""
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, start_new_session=True, env=env)
    try:
        out, err = p.communicate(data, timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        p.communicate()
        raise ChildTimeout(f"phase '{phase}': the process did not finish within {timeout} s")
    return subprocess.CompletedProcess(cmd, p.returncode, out, err)


def mutant_env() -> dict:
    """Environment for running a corrupted engine (`--mutants`, `--mutate`): the test
    mode is enabled only explicitly (docs/format.md, section 8, "Test mode")."""
    return dict(os.environ, FIGRAM_ALLOW_MUTANTS="1")


def default_timeout() -> float:
    return float(os.environ.get("FIGRAM_TEST_TIMEOUT", "600"))

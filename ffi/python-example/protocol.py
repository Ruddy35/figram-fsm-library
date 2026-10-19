"""Строгая проверка ответов протокола движка (FORMAT.md §7) и запуск дочерних
процессов с жёстким сроком. Используется клиентом и всеми тестами.

Правило: ответ — ровно один из вариантов «результат», «need», «error»;
типы полей проверяются точно (булево значение не считается числом).
"""

from __future__ import annotations

import os
import signal
import subprocess

MAX_INT = 9007199254740991


class ProtocolError(Exception):
    """Ответ движка нарушает протокол."""


class ChildTimeout(Exception):
    """Дочерний процесс не уложился в срок."""


def is_int(x) -> bool:
    return type(x) is int


def nat(x, what: str) -> None:
    if not is_int(x) or not 0 <= x <= MAX_INT:
        raise ProtocolError(f"{what}: ожидалось целое 0…2^53−1, получено {x!r}")


def name(x, what: str) -> None:
    if type(x) is not str or not x:
        raise ProtocolError(f"{what}: ожидалась непустая строка, получено {x!r}")


def check_snapshot(s) -> None:
    """Строго по snapshot.schema.json."""
    if type(s) is not dict:
        raise ProtocolError(f"снимок не объект: {s!r}")
    extra = set(s) - {"v", "at", "resume", "timers", "seq"}
    if extra:
        raise ProtocolError(f"снимок: лишние поля {sorted(extra)}")
    if "v" in s and (not is_int(s["v"]) or s["v"] != 1):
        raise ProtocolError(f"снимок: версия {s['v']!r}")
    name(s.get("at"), "снимок.at")
    for k, field in (("resume", "resume"), ("timers", "timers")):
        if k in s and type(s[k]) is not dict:
            raise ProtocolError(f"снимок.{field} не объект")
    for k, v in s.get("resume", {}).items():
        name(k, "снимок.resume: раздел")
        name(v, f"снимок.resume.{k}")
    for k, v in s.get("timers", {}).items():
        name(k, "снимок.timers: имя")
        nat(v, f"снимок.timers.{k}")
    if "seq" in s:
        nat(s["seq"], "снимок.seq")


ACTION_FORMS = {
    "send": {"send"},
    "call": {"call"},
    "start_timer": {"start_timer", "ms", "token"},
    "cancel_timer": {"cancel_timer"},
}


def check_actions(acts) -> None:
    if type(acts) is not list:
        raise ProtocolError(f"actions не список: {acts!r}")
    for a in acts:
        if type(a) is not dict:
            raise ProtocolError(f"действие не объект: {a!r}")
        kinds = [k for k in ACTION_FORMS if k in a]
        if len(kinds) != 1 or set(a) != ACTION_FORMS[kinds[0]]:
            raise ProtocolError(f"неизвестная форма действия: {a!r}")
        name(a[kinds[0]], f"действие {kinds[0]}")
        if kinds[0] == "start_timer":
            nat(a["ms"], "start_timer.ms")
            nat(a["token"], "start_timer.token")


def check_reply(resp, rid: int, op: str) -> str:
    """Проверить ответ на запрос `op` с id `rid`; вернуть вариант:
    'result', 'need', 'error' или 'hello'."""
    if type(resp) is not dict:
        raise ProtocolError(f"ответ не объект: {resp!r}")
    if not is_int(resp.get("id")) or resp["id"] != rid:
        raise ProtocolError(f"id ответа {resp.get('id')!r} вместо {rid}")
    keys = set(resp) - {"id"}
    if keys == {"error"}:
        if type(resp["error"]) is not str:
            raise ProtocolError("error не строка")
        return "error"
    if op == "hello":
        if keys != {"protocol", "states"} or not is_int(resp["protocol"]) or not is_int(resp["states"]):
            raise ProtocolError(f"ответ hello: {resp!r}")
        return "hello"
    if op == "step" and keys == {"need"}:
        need = resp["need"]
        if type(need) is not list or not need or len(set(need)) != len(need):
            raise ProtocolError(f"need: {need!r}")
        for n in need:
            name(n, "need")
        return "need"
    if keys not in ({"snapshot", "actions"}, {"snapshot", "actions", "recovered"}):
        raise ProtocolError(f"ответ {op}: поля {sorted(keys)}")
    if "recovered" in resp and resp["recovered"] is not True:
        raise ProtocolError(f"recovered: {resp['recovered']!r}")
    check_snapshot(resp["snapshot"])
    check_actions(resp["actions"])
    return "result"


def run_child(cmd, data: bytes, timeout: float, phase: str) -> subprocess.CompletedProcess:
    """Запустить процесс с данными на вход; по истечении срока убить его
    вместе с потомками и выбросить ChildTimeout с названием фазы."""
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, start_new_session=True)
    try:
        out, err = p.communicate(data, timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        p.communicate()
        raise ChildTimeout(f"фаза «{phase}»: процесс не завершился за {timeout} с")
    return subprocess.CompletedProcess(cmd, p.returncode, out, err)


def default_timeout() -> float:
    return float(os.environ.get("FIGRAM_TEST_TIMEOUT", "600"))

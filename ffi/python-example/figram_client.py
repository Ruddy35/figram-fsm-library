"""Клиент figram-engine для Python: пул процессов, общение через stdin/stdout.

Каждый процесс движка однопоточный и обрабатывает запросы по очереди.
Клиент отправляет запросы, не дожидаясь ответов на предыдущие (конвейер):
у каждого запроса свой `id`, отдельный поток читает ответы и будит того,
кто ждёт. Параллельность — через несколько процессов (по числу ядер).

Пользователь всегда попадает в один и тот же процесс (по хешу ключа),
поэтому его события обрабатываются строго по порядку отправки.
"""

from __future__ import annotations

import itertools
import json
import os
import queue
import signal
import subprocess
import threading
import time
import zlib
from typing import Callable, Iterable

from protocol import ProtocolError, check_reply


class EngineError(Exception):
    pass


# FormatIO.maxDepth: наибольшая вложенность массивов и объектов в строке запроса.
MAX_DEPTH = 256


def _check_wire(x, depth: int = 0) -> None:
    """Запрос, который движок разберёт на уровне текста.

    Строку, которую разобрать нельзя (повторяющийся ключ, вложенность больше
    MAX_DEPTH, одиночный суррогат), движок отвергает ответом без id
    (`{"id":null,...}`): к запросу его не привязать, и клиенту пришлось бы
    считать его нарушением протокола и убивать общий процесс вместе с
    запросами других пользователей. Поэтому такие запросы отвергаются здесь:
    ключи — только строки (json.dumps превращает 1 и "1", True и "true" в
    одинаковые ключи), вложенность ограничена; суррогаты отсекает кодирование.
    """
    if isinstance(x, dict):
        if depth >= MAX_DEPTH:
            raise ValueError(f"вложенность больше {MAX_DEPTH}")
        for k, v in x.items():
            if not isinstance(k, str):
                raise TypeError(f"ключ {k!r} не строка")
            _check_wire(v, depth + 1)
    elif isinstance(x, (list, tuple)):
        if depth >= MAX_DEPTH:
            raise ValueError(f"вложенность больше {MAX_DEPTH}")
        for v in x:
            _check_wire(v, depth + 1)


class _Process:
    """Один процесс движка.

    * Запись в stdin делает отдельный поток-писатель; вызывающий поток никогда
      не блокируется на вводе-выводе и ждёт ответ не дольше `timeout` от начала
      вызова, включая время, пока запрос стоит в очереди на запись.
    * Замок защищает только таблицу ожидающих запросов, ввод-вывод под ним
      не выполняется, поэтому аварийная остановка никогда его не ждёт.
    * Любое нарушение протокола (невалидный JSON, чужой или нецелый id, ответ
      неправильной формы для операции, обрыв вывода, таймаут) помечает процесс
      неисправным, будит все ожидающие запросы ошибкой и убивает процесс.
    """

    def __init__(self, binary: str, document: str, timeout: float) -> None:
        self._p = subprocess.Popen(
            [binary, document],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        self._timeout = timeout
        self._lock = threading.Lock()
        self._ids = itertools.count(1)
        self._pending: dict[int, list] = {}
        self._outbox: queue.SimpleQueue = queue.SimpleQueue()
        self.failure: str | None = None
        self._writer = threading.Thread(target=self._write_loop, daemon=True)
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._writer.start()
        self._reader.start()

    @property
    def alive(self) -> bool:
        return self.failure is None

    def _kill(self) -> None:
        try:
            os.killpg(self._p.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        # Собрать процесс сразу, не блокируя вызывающий поток (без зомби).
        threading.Thread(target=self._p.wait, daemon=True).start()

    def _fail(self, reason: str) -> None:
        """Пометить процесс неисправным, разбудить всех ожидающих, убить процесс.

        Убивает только первый вызов: следующие (читатель видит EOF, писатель —
        EPIPE) приходят, когда фоновый wait() мог уже собрать процесс, и pid
        свободен — SIGKILL по нему попал бы в чужую группу процессов."""
        with self._lock:
            first = self.failure is None
            if first:
                self.failure = reason
            pending, self._pending = self._pending, {}
        for slot in pending.values():
            slot[0].set()
        if first:
            self._outbox.put(None)
            self._kill()

    def call(self, request: dict) -> dict:
        deadline = time.monotonic() + self._timeout
        # Сериализация и кодирование до регистрации: неверный аргумент — ошибка
        # вызова, таблица ожидающих и процесс не затрагиваются. UnicodeEncodeError
        # (одиночный суррогат) — подкласс ValueError.
        try:
            _check_wire(request)
            body = json.dumps(request, ensure_ascii=False, separators=(",", ":"),
                              allow_nan=False).encode()
        except (TypeError, ValueError, RecursionError) as e:
            raise EngineError(f"запрос не сериализуется в JSON: {e}") from None
        done = threading.Event()
        slot: list = [done, None, request["op"]]
        with self._lock:
            if self.failure is not None:
                raise EngineError(f"процесс движка неисправен: {self.failure}")
            rid = next(self._ids)
            self._pending[rid] = slot
        # id дописывается к уже сериализованному объекту: {"id":N,...}
        line = b'{"id":' + str(rid).encode() + (b"," + body[1:] if body != b"{}" else b"}")
        self._outbox.put(line + b"\n")
        if not done.wait(max(0.0, deadline - time.monotonic())):
            self._fail(f"нет ответа за {self._timeout} с")
        if slot[1] is None:
            raise EngineError(f"процесс движка неисправен: {self.failure}")
        return slot[1]

    def _write_loop(self) -> None:
        try:
            while True:
                line = self._outbox.get()
                if line is None:
                    return
                try:
                    self._p.stdin.write(line)
                    self._p.stdin.flush()
                except (OSError, ValueError) as e:
                    self._fail(f"запись не удалась: {e!r}")
                    return
        finally:
            # Вход закрывает только писатель. Остаток буфера в мёртвый процесс
            # не отправить — ошибку оборванного канала подавляем здесь, иначе
            # её напечатает финализатор.
            try:
                self._p.stdin.close()
            except (OSError, ValueError):
                pass

    def _read_loop(self) -> None:
        try:
            for raw in self._p.stdout:
                try:
                    response = json.loads(raw)
                except ValueError as e:
                    self._fail(f"невалидный JSON: {e!r} в {raw[:200]!r}")
                    return
                rid = response.get("id") if type(response) is dict else None
                if type(rid) is not int:
                    self._fail(f"ответ без целого id: {raw[:200]!r}")
                    return
                with self._lock:
                    slot = self._pending.pop(rid, None)
                if slot is None:
                    self._fail(f"ответ с неизвестным id {rid}")
                    return
                try:
                    check_reply(response, rid, slot[2])
                except ProtocolError as e:
                    self._fail(f"нарушение протокола: {e}")
                    slot[0].set()
                    return
                slot[1] = response
                slot[0].set()
            self._fail("движок закрыл вывод")
        except Exception as e:  # noqa: BLE001 — любой сбой чтения
            self._fail(f"сбой чтения: {e!r}")

    def close(self) -> None:
        """Завершить процесс; не дольше `timeout` даже если движок завис."""
        with self._lock:
            # Новые вызовы — ошибка; поздний _fail читателя (EOF после выхода
            # движка) не будет убивать уже собранный здесь pid.
            if self.failure is None:
                self.failure = "клиент закрыт"
                closing = True
            else:
                closing = False
        self._outbox.put(None)
        self._writer.join(self._timeout)
        try:
            self._p.wait(timeout=self._timeout)
        except subprocess.TimeoutExpired:
            if closing:
                self._kill()   # процесс жив и не собран: pid ещё наш
            self._p.wait()


class Engine:
    """Пул процессов `figram-engine`. Безопасен для вызова из многих потоков."""

    def __init__(self, binary: str, document: str, processes: int = 1,
                 timeout: float = 10.0) -> None:
        self._args = (binary, document, timeout)
        self._procs = [_Process(*self._args) for _ in range(processes)]
        self._swap = threading.Lock()
        self._closed = False

    def _proc(self, key: str) -> _Process:
        """Процесс пользователя; неисправный заменяется новым (текущие запросы
        к старому уже завершены ошибкой, хост повторит их по контракту)."""
        if self._closed:
            raise EngineError("клиент закрыт")
        i = zlib.crc32(key.encode()) % len(self._procs)
        p = self._procs[i]
        if not p.alive:
            with self._swap:
                if self._closed:
                    raise EngineError("клиент закрыт")
                if not self._procs[i].alive:
                    try:
                        self._procs[i] = _Process(*self._args)
                    except OSError as e:   # нет бинарника, нет прав, EMFILE, EAGAIN
                        raise EngineError(f"не удалось запустить движок: {e}") from None
                p = self._procs[i]
        return p

    def start(self, key: str) -> dict:
        return self._check(self._proc(key).call({"op": "start"}))

    def restart(self, key: str, snapshot: dict) -> dict:
        """Начать пользователя заново, не сбрасывая счётчик жетонов (HOST.md)."""
        return self._check(self._proc(key).call({"op": "restart", "snapshot": snapshot}))

    def step(
        self,
        key: str,
        snapshot: dict,
        event: str,
        guards: Callable[[Iterable[str]], dict[str, bool]] | None = None,
        token: int | None = None,
    ) -> dict:
        """Шаг автомата. `guards(names)` вычисляет условия разработчика —
        вызывается только если движок сообщил, что они нужны для этого шага.
        Для события таймера `timer:<имя>` передайте `token` из его start_timer."""
        proc = self._proc(key)
        request = {"op": "step", "snapshot": snapshot, "event": event}
        if token is not None:
            request["token"] = token
        response = proc.call(request)
        if "need" in response:
            need = response["need"]
            if guards is None:
                raise EngineError(f"нужны условия: {need}")
            values = guards(list(need))
            if type(values) is not dict:
                raise EngineError(f"функция условий вернула {type(values).__name__}, нужен dict")
            missing = [n for n in need if n not in values]
            wrong = [n for n in need if n in values and type(values[n]) is not bool]
            if missing or wrong:
                raise EngineError(f"функция условий: нет значений {missing}, не bool {wrong}")
            response = proc.call({**request, "guards": {n: values[n] for n in need}})
            if "need" in response:
                raise EngineError(f"движок повторно запросил условия: {response['need']}")
        response = self._check(response)
        if "snapshot" not in response:
            raise EngineError(f"незавершённый ответ шага: {response}")
        return response

    @staticmethod
    def _check(response: dict) -> dict:
        if "error" in response:
            raise EngineError(response["error"])
        return response

    def close(self) -> None:
        """Закрыть пул: после него вызовы — EngineError, процессы не запускаются."""
        with self._swap:
            self._closed = True
        for p in self._procs:
            p.close()

"""Клиент figram-engine для Python: пул процессов, общение через stdin/stdout.

Каждый процесс движка однопоточный, обрабатывает запросы по очереди и отвечает
строго в порядке запросов (FORMAT.md §7). Клиент отправляет запросы, не
дожидаясь ответов на предыдущие (конвейер): у каждого запроса свой `id`,
отдельный поток читает ответы и будит того, кто ждёт. Параллельность — через
несколько процессов (по числу ядер).

Пользователь всегда попадает в один и тот же процесс (по хешу ключа) — это
только распределение нагрузки. Порядок событий пользователя и целостность его
снимка клиент НЕ обеспечивает: движок не хранит состояния, и два события
одного пользователя, отправленные без блокировки, считаются от одного снимка —
одно из них теряется; при сбое процесса все его запросы завершаются ошибкой, и
повторы хоста могут прийти в любом порядке. Хост обрабатывает события
пользователя по одному, под блокировкой на пользователя (HOST.md §1.1).

Жизненный цикл процесса (HOST.md §5):

* Запуск. Процесс загружает документ (на больших документах — секунды и
  минуты); клиент сразу отправляет `hello` и ждёт ответа не дольше
  `start_timeout`. Запросы процесс принимает только после ответа; вызовы,
  пришедшие во время загрузки, ждут готовности, и их `timeout` отсчитывается
  от неё. Таймауты запросов загружающийся процесс не убивают.
* Таймаут запроса завершает ошибкой только этот вызов: его `id` помечается
  брошенным, поздний ответ отбрасывается, процесс продолжает работать.
  Зависшим процесс считается, только если при неотвеченных запросах он
  ничего не выводит `hang_timeout` секунд.
* Нарушение протокола (невалидный JSON или UTF-8, строка ответа длиннее
  `max_line`, чужой или нецелый id, ответ не по порядку, ответ неправильной
  формы, обрыв вывода, падение, нет ответа на `hello`) и зависание помечают
  процесс неисправным, будят все ожидающие вызовы ошибкой и убивают процесс.
  Исключение — ответ-ошибка с `"id": null` (движок не разобрал строку
  запроса): ответы идут по порядку, поэтому он завершает ошибкой только самый
  старый неотвеченный запрос.
* Замена. Следующий вызов к неисправному процессу запускает новый и ждёт его
  загрузки. Если процессы слота падают подряд, не обслужив ни одного запроса
  (например, документ не загружается), перезапуск откладывается с
  экспоненциальной паузой `restart_backoff`; в паузе вызовы сразу получают
  `EngineError` с причиной последнего сбоя, включая хвост stderr движка.
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
    """Отказ вызова движка.

    * `retryable` — True: сбой процесса или транспорта (нет ответа за срок,
      процесс упал, нарушил протокол, не загрузился, перезапускается): шаг —
      чистая функция, хост повторяет его (HOST.md §5). False — отказ
      детерминирован: ответ движка с `error` (снимок отвергнут, `seq_overflow`,
      строка не разобрана) или аргумент, который нельзя отправить; повтор
      даст то же самое.
    * `code` — машинный код из ответа движка (`parse_error`, `too_large`,
      `bad_request`, `bad_snapshot`, `seq_overflow`); None, если ответа с
      ошибкой не было или движок старый, без поля `code`.
    """

    def __init__(self, message: str, *, code: str | None = None, retryable: bool = True) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


PROTOCOL = 1
# FormatIO.maxDepth: наибольшая вложенность массивов и объектов в строке запроса.
MAX_DEPTH = 256
# Наибольшая длина строки ответа по умолчанию; длиннее — нарушение протокола.
MAX_LINE = 16 * 1024 * 1024
STDERR_TAIL = 2048        # сколько последних байт stderr движка попадает в ошибки
_TICK = 0.25              # период проверки сроков потоком чтения
_DRAIN = 5.0              # после сбоя вывод дочитывается до EOF не дольше
_HELLO_ID = 0             # id запроса hello при запуске; вызовы — с 1
_CLOSED = "клиент закрыт"
_MISSING = object()
# Экранированный суррогат в тексте ответа (\ud800…\udfff): только тогда нужна
# поэлементная проверка, что одиночных суррогатов нет.
_ESC_SURROGATE = re.compile(r"\\u[dD][89a-fA-F]")


def _check_wire(x, depth: int = 0) -> None:
    """Запрос, который движок разберёт на уровне текста.

    Строку, которую разобрать нельзя (повторяющийся ключ, вложенность больше
    MAX_DEPTH, одиночный суррогат), движок отвергает ответом-ошибкой, старый
    движок — ещё и без id. Такие запросы отвергаются здесь, до отправки:
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


def _decode(raw: bytes):
    """Строка ответа → значение JSON, строго: только UTF-8 и без одиночных
    суррогатов — ни закодированных байтами (CESU), ни экранированных."""
    text = raw.decode("utf-8")          # UnicodeDecodeError ⊂ ValueError
    value = json.loads(text)
    if _ESC_SURROGATE.search(text):
        # Пару суррогатов json склеивает в один символ; одиночный не кодируется.
        json.dumps(value, ensure_ascii=False).encode("utf-8")
    return value


def _error_reply(resp: dict) -> bool:
    """Ответ-ошибка: ровно {id, error} (старый протокол) или {id, error, code}."""
    if "error" not in resp:
        return False
    keys = resp.keys() - {"id"}
    if keys not in ({"error"}, {"error", "code"}):
        raise ProtocolError(f"ответ с error и лишними полями: {sorted(keys)}")
    if type(resp["error"]) is not str:
        raise ProtocolError("error не строка")
    if "code" in resp and (type(resp["code"]) is not str or not resp["code"]):
        raise ProtocolError(f"code: {resp['code']!r}")
    return True


def _check_hello(resp: dict) -> None:
    keys = resp.keys() - {"id"}
    # "mutant" — только у испорченного движка тестов (FIGRAM_ALLOW_MUTANTS).
    if not {"protocol", "states"} <= keys <= {"protocol", "states", "mutant"}:
        raise ProtocolError(f"ответ hello: поля {sorted(keys)}")
    if type(resp["protocol"]) is not int or type(resp["states"]) is not int:
        raise ProtocolError(f"ответ hello: {resp!r}")
    if "mutant" in resp and type(resp["mutant"]) is not str:
        raise ProtocolError(f"ответ hello: mutant {resp['mutant']!r}")
    if resp["protocol"] != PROTOCOL:
        raise ProtocolError(f"протокол движка {resp['protocol']}, клиент знает только {PROTOCOL}")


class _Config:
    __slots__ = ("timeout", "start_timeout", "hang_timeout", "max_line")

    def __init__(self, timeout, start_timeout, hang_timeout, max_line) -> None:
        self.timeout = timeout
        self.start_timeout = start_timeout
        self.hang_timeout = hang_timeout
        self.max_line = max_line


class _Call:
    """Запрос в пути. kind: "user" — вызов хоста; "start" — hello при запуске;
    "probe" — hello проверки живости после таймаута."""
    __slots__ = ("done", "response", "op", "kind", "sent", "abandoned")

    def __init__(self, op: str, kind: str = "user") -> None:
        self.done = threading.Event()
        self.response = None
        self.op = op
        self.kind = kind
        self.sent = False          # записан в stdin: ответ на него обязателен
        self.abandoned = False     # вызов ушёл по таймауту: поздний ответ отбросить


class _Process:
    """Один процесс движка.

    * Запись в stdin делает отдельный поток-писатель; вызывающий поток никогда
      не блокируется на вводе-выводе и ждёт ответ не дольше `timeout` от начала
      вызова (от готовности, если процесс ещё загружается), включая время,
      пока запрос стоит в очереди на запись.
    * Поток чтения читает stdout и stderr (хвост — для текста ошибок), следит
      за сроками запуска и зависания и ограничивает длину строки ответа.
    * Замок защищает только таблицы запросов, ввод-вывод под ним не
      выполняется, поэтому аварийная остановка никогда его не ждёт.
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
        self._pending: dict[int, _Call] = {}     # id → запрос без ответа
        self._sent: collections.deque = collections.deque()   # id записанных, по порядку
        self._outbox: queue.SimpleQueue = queue.SimpleQueue()
        self.failure: str | None = None
        self.failed_at: float | None = None
        self.ready_at: float | None = None
        self.served = False                      # ответил хотя бы на один вызов
        self._started = threading.Event()        # загрузка закончилась: готов или сбой
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

    # --- сбой и остановка -------------------------------------------------

    def _peek_exit(self) -> None:
        """Запомнить код выхода, если процесс уже завершился сам. Без сбора
        (WNOWAIT): pid остаётся нашим до wait()."""
        if self._exit is not None:
            return
        try:
            st = os.waitid(os.P_PID, self._p.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
        except (AttributeError, OSError):
            return
        if st is None:
            return
        if st.si_code == os.CLD_EXITED:
            self._exit = f"движок завершился с кодом {st.si_status}"
        elif st.si_code in (os.CLD_KILLED, os.CLD_DUMPED):
            self._exit = f"движок убит сигналом {st.si_status}"

    def _kill(self) -> None:
        self._peek_exit()
        try:
            os.killpg(self._p.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        # Собрать процесс сразу, не блокируя вызывающий поток (без зомби).
        threading.Thread(target=self._p.wait, daemon=True).start()

    def _fail(self, reason: str, kill: bool = True) -> bool:
        """Пометить процесс неисправным, разбудить всех ожидающих, убить процесс.

        Убивает только первый вызов: следующие (читатель видит EOF, писатель —
        EPIPE) приходят, когда фоновый wait() мог уже собрать процесс, и pid
        свободен — SIGKILL по нему попал бы в чужую группу процессов.
        Возвращает True, если этот вызов был первым."""
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
        """Причина сбоя с кодом выхода и хвостом stderr движка."""
        if self.failure != _CLOSED and threading.current_thread() is not self._reader:
            self._reader.join(0.25)      # дочитать stderr упавшего процесса
        text = self.failure or "неизвестный сбой"
        if self._exit:
            text += f"; {self._exit}"
        tail = self.stderr_tail()
        if tail:
            text += f"; stderr: {tail}"
        return text

    def _failed_error(self) -> EngineError:
        return EngineError(f"процесс движка неисправен: {self.error_text()}",
                           retryable=self.failure != _CLOSED)

    # --- вызовы -----------------------------------------------------------

    def wait_ready(self) -> None:
        """Дождаться конца загрузки (ответа на hello); EngineError, если процесс
        не запустился. Срок запуска соблюдает поток чтения."""
        if self.ready_at is None and self.failure is None:
            t = self._cfg.start_timeout
            self._started.wait(None if t is None else
                               max(0.0, self.spawned_at + t - time.monotonic()) + 1.0)
        if self.failure is not None:
            raise self._failed_error()
        if self.ready_at is None:
            self._fail(f"движок не ответил на hello за {self._cfg.start_timeout} с")
            raise self._failed_error()

    def call(self, request: dict, deadline: float | None = None) -> dict:
        """Отправить запрос и дождаться ответа: не дольше `timeout` от начала
        вызова или от готовности процесса и не позже `deadline` (monotonic)."""
        # Сериализация и кодирование до регистрации: неверный аргумент — ошибка
        # вызова, таблица ожидающих и процесс не затрагиваются. UnicodeEncodeError
        # (одиночный суррогат) — подкласс ValueError.
        try:
            _check_wire(request)
            body = json.dumps(request, ensure_ascii=False, separators=(",", ":"),
                              allow_nan=False).encode()
        except (TypeError, ValueError, RecursionError) as e:
            raise EngineError(f"запрос не сериализуется в JSON: {e}", retryable=False) from None
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
        # id дописывается к уже сериализованному объекту: {"id":N,...}
        line = b'{"id":' + str(rid).encode() + (b"," + body[1:] if body != b"{}" else b"}")
        self._outbox.put((rid, line + b"\n"))
        if not c.done.wait(max(0.0, end - time.monotonic())):
            with self._lock:
                mine = c.response is None and self._pending.get(rid) is c
                if mine:
                    if c.sent:
                        c.abandoned = True       # ответ придёт — отбросить
                    else:
                        del self._pending[rid]   # не записан — писатель пропустит
            if mine:
                self._probe_health()
                raise EngineError(f"нет ответа за {limit:.3g} с")
        if c.response is None:
            raise self._failed_error()
        return c.response

    def _probe_health(self) -> None:
        """После таймаута — hello в очередь (не больше одного сразу): ответ
        подтверждает, что процесс жив и отвечает по порядку."""
        with self._lock:
            if self.failure is not None or self._probe is not None:
                return
            rid = next(self._ids)
            self._probe = rid
            self._pending[rid] = _Call("hello", "probe")
        self._outbox.put((rid, b'{"id":%d,"op":"hello"}\n' % rid))

    # --- потоки -----------------------------------------------------------

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
                    if c is None:            # снят до записи: таймаут, сбой, close
                        continue
                    if not self._sent:
                        self._last_progress = time.monotonic()
                    self._sent.append(rid)
                    c.sent = True
                try:
                    stdin.write(line)
                    stdin.flush()
                except (OSError, ValueError) as e:
                    self._fail(f"запись не удалась: {e!r}")
                    return
        finally:
            # Вход закрывает только писатель. Остаток буфера в мёртвый процесс
            # не отправить — ошибку оборванного канала подавляем здесь, иначе
            # её напечатает финализатор.
            try:
                stdin.close()
            except (OSError, ValueError):
                pass

    def _check_deadlines(self) -> float:
        """Срок запуска (до готовности) и зависания (после); вернуть, сколько
        можно ждать вывода до следующей проверки."""
        now = time.monotonic()
        cfg = self._cfg
        if self.ready_at is None:
            if cfg.start_timeout is None:
                return _TICK
            left = self.spawned_at + cfg.start_timeout - now
            if left <= 0:
                self._fail(f"движок не ответил на hello за {cfg.start_timeout} с (загрузка документа)")
            return max(0.0, min(_TICK, left))
        with self._lock:
            busy, last = bool(self._sent), self._last_progress
        if not busy:
            return _TICK
        left = last + cfg.hang_timeout - now
        if left <= 0:
            self._fail(f"движок завис: нет вывода {cfg.hang_timeout} с при неотвеченных запросах")
        return max(0.0, min(_TICK, left))

    def _read_loop(self) -> None:
        out, err = self._p.stdout, self._p.stderr
        ofd, efd = out.fileno(), err.fileno()
        poller = select.poll()
        for fd in (ofd, efd):
            poller.register(fd, select.POLLIN)
        open_fds = {ofd, efd}
        frags: list[bytes] = []          # начало строки ответа без конца строки
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
                    # После сбоя: дочитать вывод до EOF (хвост stderr; процесс,
                    # который при close ещё пишет ответы, не блокируется).
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
                            # Последняя строка без перевода строки принимается.
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
                            self._fail(f"строка ответа длиннее {max_line} байт")
                            break
                        if not self._on_line(line):
                            break
                    if self.failure is None and start < len(chunk):
                        frags.append(chunk[start:])
                        flen += len(chunk) - start
                        if max_line and flen > max_line:
                            self._fail(f"строка ответа длиннее {max_line} байт (конца строки нет)")
                    if self.failure is not None:
                        frags, flen = [], 0
        except Exception as e:  # noqa: BLE001 — любой сбой чтения
            self._fail(f"сбой чтения: {e!r}")
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
        """stdout закрыт: коротко дочитать stderr (там причина, например ошибка
        документа) и код выхода, затем сбой."""
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
        for _ in range(5):          # stderr закрыт — процесс вот-вот станет зомби
            self._peek_exit()
            if self._exit is not None or efd in open_fds:
                break
            time.sleep(0.01)
        self._fail("движок закрыл вывод")

    def _on_line(self, raw: bytes) -> bool:
        """Разобрать строку ответа; False — процесс помечен неисправным."""
        try:
            resp = _decode(raw)
        except (ValueError, RecursionError) as e:
            self._fail(f"невалидный ответ (JSON или UTF-8): {e!r} в {raw[:200]!r}")
            return False
        if type(resp) is not dict:
            self._fail(f"ответ не объект: {raw[:200]!r}")
            return False
        try:
            is_error = _error_reply(resp)
        except ProtocolError as e:
            self._fail(f"нарушение протокола: {e}")
            return False
        rid = resp.get("id", _MISSING)
        with self._lock:
            if self.failure is not None:
                return False
            head = self._sent[0] if self._sent else None
            if rid is None and is_error and head is not None:
                # Строку не разобрали, id неизвестен: ответы идут строго по
                # порядку запросов — это ответ на самый старый неотвеченный.
                rid = head
            c = self._pending.get(rid) if type(rid) is int else None
        if type(rid) is not int:
            self._fail(f"ответ без целого id: {raw[:200]!r}")
            return False
        if c is None:
            self._fail(f"ответ с неизвестным id {rid}")
            return False
        if rid != head:
            self._fail(f"ответ на запрос {rid} раньше, чем на {head}: нарушен порядок ответов")
            return False
        try:
            if is_error:
                pass
            elif c.op == "hello":
                _check_hello(resp)
            else:
                check_reply(resp, rid, c.op)
        except ProtocolError as e:
            self._fail(f"нарушение протокола: {e}")
            return False
        with self._lock:
            if self._pending.get(rid) is not c:      # сбой между проверками
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
            self._fail(f"движок отверг hello: {resp['error']}")
            return False
        elif c.kind == "start":
            self.ready_at = time.monotonic()
            self._started.set()
        return True

    def _begin_close(self) -> None:
        # Новые вызовы — ошибка, ожидающие будятся сразу. Готовый процесс не
        # убивается: писатель закрывает stdin, движок выходит по EOF. Ещё не
        # загрузившийся убивается сразу: вход он прочтёт только после загрузки,
        # а запросов в работе у него нет.
        loading = self.ready_at is None
        first = self._fail(_CLOSED, kill=loading)
        self._closing = first and not loading     # жив, не убит: остановка за close

    def _finish_close(self, deadline: float) -> None:
        self._writer.join(max(0.0, deadline - time.monotonic()))
        try:
            self._p.wait(timeout=max(0.0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            if self._closing:
                self._kill()   # жив и не собран: pid ещё наш
            self._p.wait()

    def close(self, deadline: float | None = None) -> None:
        """Завершить процесс; не дольше `timeout` (или до `deadline`), даже если
        движок завис."""
        self._begin_close()
        self._finish_close(time.monotonic() + self._timeout if deadline is None else deadline)


class Engine:
    """Пул процессов `figram-engine`. Безопасен для вызова из многих потоков.

    * `timeout` — срок одного обращения к движку: от начала вызова или от
      готовности процесса, если он ещё загружается;
    * `start_timeout` — срок загрузки документа (ответа на hello) у каждого
      процесса; None — без предела;
    * `hang_timeout` — процесс, который при неотвеченных запросах ничего не
      выводит столько секунд, считается зависшим и заменяется; None — по
      умолчанию max(60, 3 × timeout); math.inf — никогда;
    * `step_timeout` — общий срок `step` от готовности процесса: оба обращения
      к движку (запрос условий и повтор с ними) и функция условий; None —
      2 × timeout;
    * `max_line` — наибольшая длина строки ответа в байтах (0 — без предела);
    * `restart_backoff` — (начальная, наибольшая) пауза в секундах перед
      повторным перезапуском слота, процессы которого падают, не обслужив ни
      одного запроса; удваивается с каждым таким сбоем, сбрасывается, когда
      процесс слота ответил на запрос; (0, 0) — без пауз;
    * `wait_ready` — конструктор ждёт загрузки всех процессов и бросает
      EngineError (с хвостом stderr движка), если какой-то не запустился;
      False — вернуться сразу, вызовы сами дождутся готовности.
    """

    def __init__(self, binary: str, document: str, processes: int = 1,
                 timeout: float = 10.0, *, start_timeout: float | None = 600.0,
                 hang_timeout: float | None = None, step_timeout: float | None = None,
                 max_line: int = MAX_LINE, restart_backoff: tuple[float, float] = (0.1, 30.0),
                 wait_ready: bool = True) -> None:
        if processes < 1:
            raise ValueError("processes должно быть не меньше 1")
        if hang_timeout is None:
            hang_timeout = max(60.0, 3 * timeout)
        self._cfg = _Config(timeout, start_timeout, hang_timeout, max_line)
        self._step_timeout = 2 * timeout if step_timeout is None else step_timeout
        self._backoff = restart_backoff
        self._args = (binary, document)
        self._swap = threading.Lock()
        self._closed = False
        self._procs: list[_Process] = []
        self._fails = [0] * processes                # подряд упавших процессов слота
        self._retry_at = [0.0] * processes           # раньше этого слот не перезапускать
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
                    raise EngineError(f"движок не запустился: {e}") from None

    def _delay(self, fails: int) -> float:
        base, cap = self._backoff
        if fails <= 0 or base <= 0:
            return 0.0
        return min(cap, base * 2 ** min(fails - 1, 60))

    def _proc(self, key: str) -> _Process:
        """Процесс пользователя; неисправный заменяется новым (текущие запросы
        к старому уже завершены ошибкой, хост повторит их по контракту)."""
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
            if self._seen[i] is not p:       # этот сбой ещё не учтён: назначить паузу
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
                except OSError as e:   # нет бинарника, нет прав, EMFILE, EAGAIN
                    self._retry_at[i] = now + self._delay(self._fails[i])
                    self._fails[i] += 1
                    spawn_error = e
                else:
                    return self._procs[i]
        if spawn_error is not None:
            raise EngineError(f"не удалось запустить движок: {spawn_error}") from None
        raise EngineError(f"процесс движка перезапустится через {wait:.1f} с; "
                          f"последний сбой: {p.error_text()}")

    def start(self, key: str, seq: int | None = None) -> dict:
        """Новый пользователь. `seq` — номер последнего перехода, с которого
        продолжить счётчик (сброс существующего пользователя, HOST.md §4; нужен
        движок, принимающий `start` с `seq`)."""
        request = {"op": "start"}
        if seq is not None:
            request["seq"] = seq
        return self._check(self._proc(key).call(request))

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
        Для события таймера `timer:<имя>` передайте `token` из его start_timer.
        Весь шаг укладывается в `step_timeout` от готовности процесса; функцию
        условий прервать нельзя, но если она вернулась после срока, второй
        запрос не отправляется — EngineError."""
        proc = self._proc(key)
        request = {"op": "step", "snapshot": snapshot, "event": event}
        if token is not None:
            request["token"] = token
        proc.wait_ready()
        deadline = time.monotonic() + self._step_timeout
        response = proc.call(request, deadline)
        if "need" in response:
            need = response["need"]
            if guards is None:
                raise EngineError(f"нужны условия: {need}", retryable=False)
            values = guards(list(need))
            if type(values) is not dict:
                raise EngineError(f"функция условий вернула {type(values).__name__}, нужен dict",
                                  retryable=False)
            missing = [n for n in need if n not in values]
            wrong = [n for n in need if n in values and type(values[n]) is not bool]
            if missing or wrong:
                raise EngineError(f"функция условий: нет значений {missing}, не bool {wrong}",
                                  retryable=False)
            if time.monotonic() >= deadline:
                raise EngineError(f"шаг не уложился в {self._step_timeout:.3g} с: "
                                  "функция условий вернулась после срока")
            response = proc.call({**request, "guards": {n: values[n] for n in need}}, deadline)
            if "need" in response:
                raise EngineError(f"движок повторно запросил условия: {response['need']}")
        response = self._check(response)
        if "snapshot" not in response:
            raise EngineError(f"незавершённый ответ шага: {response}")
        return response

    @staticmethod
    def _check(response: dict) -> dict:
        if "error" in response:
            raise EngineError(response["error"], code=response.get("code"), retryable=False)
        return response

    def close(self) -> None:
        """Закрыть пул: после него вызовы — EngineError, процессы не запускаются.
        Процессы закрываются одновременно, с одним общим сроком `timeout`."""
        with self._swap:
            self._closed = True
            procs = list(self._procs)
        for p in procs:
            p._begin_close()
        deadline = time.monotonic() + self._cfg.timeout
        for p in procs:
            p._finish_close(deadline)

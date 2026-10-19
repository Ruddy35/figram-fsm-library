"""Эталонный хост: контракт обработки событий (HOST.md) и его проверка.

Модель продакшена в миниатюре:
* «база» с атомарными транзакциями: снимок пользователя, обработанные
  update_id и очередь исходящих действий (outbox) пишутся вместе;
* исполнитель outbox выполняет действия с ключами идемпотентности;
* «CRM» принимает вызов один раз на ключ;
* планировщик таймеров присылает события с жетонами.

Сбои внедряются случайно: до фиксации транзакции, после фиксации до
исполнения, между исполнением и отметкой «выполнено». Повторная доставка
и параллельная доставка событий одного пользователя — тоже.

    python3 host_sim.py ENGINE pizza.bot.json
"""

import copy
import json
import random
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from figram_client import Engine


class Crash(Exception):
    """Внедрённый сбой процесса хоста."""


class DB:
    """Хранилище с атомарными транзакциями (одна блокировка на фиксацию)."""

    def __init__(self):
        self._lock = threading.Lock()
        self.users = {}        # user -> {"snapshot": ..., "seen": set()}
        self.outbox = []       # {"key", "user", "action", "done"}

    def read(self, user):
        with self._lock:
            return copy.deepcopy(self.users.get(user, {"snapshot": None, "seen": set()}))

    def commit(self, user, record, actions):
        with self._lock:
            self.users[user] = record
            for i, a in enumerate(actions):
                self.outbox.append({"key": f"{user}:{record['snapshot']['seq']}:{i}",
                                    "user": user, "action": a, "done": False})

    def pending(self):
        with self._lock:
            return [e for e in self.outbox if not e["done"]]

    def mark_done(self, entry):
        with self._lock:
            entry["done"] = True


class CRM:
    """Внешняя система: вызов с тем же ключом выполняется один раз."""

    def __init__(self):
        self._lock = threading.Lock()
        self.executed = {}     # key -> (user, name)

    def call(self, key, user, name):
        with self._lock:
            self.executed.setdefault(key, (user, name))


class Host:
    def __init__(self, engine, crash_rate, naive=False):
        self.naive = naive         # контрольный хост без контракта
        self.crashes = 0
        self.engine = engine
        self.db = DB()
        self.crm = CRM()
        self.timers = {}       # (user, timer) -> token: «планировщик»
        self.sent = []         # отправленные сообщения
        self.crash_rate = crash_rate
        self._user_locks = {}
        self._locks_lock = threading.Lock()
        self._exec_lock = threading.Lock()
        self.order_log = {}    # user -> события в порядке обработки

    def _maybe_crash(self, where):
        if random.random() < self.crash_rate:
            self.crashes += 1
            raise Crash(where)

    def _user_lock(self, user):
        with self._locks_lock:
            return self._user_locks.setdefault(user, threading.Lock())

    def guards(self, user):
        # Условия вычисляются по данным хоста той же версии, что и снимок.
        return lambda names: {n: True for n in names}

    def handle(self, user, update_id, event, token=None):
        """Обработка одного обновления. Возвращает статус; Crash — сбой."""
        with self._user_lock(user):                     # 1. по одному на пользователя
            rec = self.db.read(user)
            if update_id in rec["seen"] and not self.naive:  # 2. повторная доставка
                return "duplicate"
            if rec["snapshot"] is None:
                r = self.engine.start(user)
            else:
                r = self.engine.step(user, rec["snapshot"], event, self.guards(user), token)
            self._maybe_crash("до фиксации")             # сбой: ничего не записано
            rec["snapshot"] = r["snapshot"]
            rec["seen"].add(update_id)
            self.db.commit(user, rec, r["actions"])      # 3. атомарно: снимок + outbox
            self.order_log.setdefault(user, []).append((event, token))
        self._maybe_crash("после фиксации")               # сбой: действия в outbox
        self.drain()
        return "ok"

    def drain(self):
        """Исполнитель outbox: каждое действие выполняется с ключом идемпотентности."""
        with self._exec_lock:
            for e in self.db.pending():
                a = e["action"]
                if "call" in a:
                    key = e["key"] if not self.naive else f"{e['key']}#{random.random()}"
                    self.crm.call(key, e["user"], a["call"])
                elif "send" in a:
                    self.sent.append((e["key"], e["user"], a["send"]))
                elif "start_timer" in a:
                    self.timers[(e["user"], a["start_timer"])] = a["token"]
                elif "cancel_timer" in a:
                    self.timers.pop((e["user"], a["cancel_timer"]), None)
                self._maybe_crash("до отметки «выполнено»")  # сбой: выполнено, не отмечено
                self.db.mark_done(e)


def deliver(host, user, update_id, event, token=None, attempts=50):
    """Доставка «как Telegram»: повторять, пока хост не подтвердит."""
    for _ in range(attempts):
        try:
            return host.handle(user, update_id, event, token)
        except Crash:
            time.sleep(0.001)
            try:
                host.drain()                              # «перезапуск» исполнителя
            except Crash:
                pass
    raise RuntimeError("не удалось доставить")


ORDER = ["/start", "btn:order", "btn:pizza", "btn:size", "btn:add",
         "btn:checkout", "msg:text", "payment:success"]


def scenario_orders(engine_path, doc, users=30, crash_rate=0.15, naive=False):
    random.seed(1)
    engine = Engine(engine_path, doc, processes=2)
    host = Host(engine, crash_rate, naive)
    # Обновления всех пользователей; каждое доставляется дважды и параллельно.
    work = [(u, i, ev) for u in range(users) for i, ev in enumerate(ORDER)]

    def user_stream(u):
        for i, ev in enumerate(ORDER):
            with ThreadPoolExecutor(2) as pool:           # дубль параллельно
                list(pool.map(lambda _: deliver(host, f"u{u}", i, ev), range(2)))

    with ThreadPoolExecutor(16) as pool:
        list(pool.map(user_stream, range(users)))
    for _ in range(100):                                  # дожать outbox после сбоев
        try:
            host.drain()
            break
        except Crash:
            pass
    host.crash_rate = 0
    host.drain()

    orders = [v for v in host.crm.executed.values() if v[1] == "create_order"]
    at_done = sum(1 for u in range(users) if host.db.users[f"u{u}"]["snapshot"]["at"] == "done")
    pending = len(host.db.pending())
    # Порядок: снимок каждого пользователя равен последовательному проигрыванию
    # его событий в порядке обработки — нет потерянных обновлений.
    lost = 0
    for u in range(users):
        snap = None
        for ev, tok in host.order_log[f"u{u}"]:
            snap = (engine.start("ref") if snap is None
                    else engine.step("ref", snap, ev, lambda ns: {n: True for n in ns}, tok))["snapshot"]
        if snap != host.db.users[f"u{u}"]["snapshot"]:
            lost += 1
    engine.close()
    print(f"{'НАИВНЫЙ хост' if naive else 'хост по контракту'}, внедрено сбоев {host.crashes}")
    print(f"  пользователей {users}: дошли до конца {at_done}, заказов в CRM {len(orders)}, "
          f"обработано событий {sum(len(v) for v in host.order_log.values())} из {len(work)} "
          f"(повторные доставки), потерянных обновлений {lost}, невыполненных действий {pending}")
    per_user = [sum(1 for o in orders if o[0] == f"u{u}") for u in range(users)]
    one_each = all(n == 1 for n in per_user)
    print("  заказы ровно по одному на пользователя:", "да" if one_each else
          f"НЕТ (у {sum(1 for n in per_user if n > 1)} пользователей повторный заказ)")
    return at_done == users and one_each and lost == 0 and pending == 0


def scenario_stale_timer(engine_path, doc):
    engine = Engine(engine_path, doc)
    host = Host(engine, 0)
    u = "t"
    deliver(host, u, 0, "/start")
    for i, ev in enumerate(["btn:order", "btn:cart"], 1):  # корзина запускает напоминание
        deliver(host, u, i, ev)
    old = host.timers[(u, "cart_reminder")]
    deliver(host, u, 3, "btn:cancel")                       # уход — таймер отменён
    deliver(host, u, 4, "btn:order")
    deliver(host, u, 5, "btn:cart")                         # новый запуск, новый жетон
    new = host.timers[(u, "cart_reminder")]
    before = host.db.users[u]["snapshot"]["at"]
    deliver(host, u, 6, "timer:cart_reminder", token=old)   # запоздавшее старое событие
    after_old = host.db.users[u]["snapshot"]["at"]
    deliver(host, u, 7, "timer:cart_reminder", token=new)
    after_new = host.db.users[u]["snapshot"]["at"]
    engine.close()
    print(f"таймер: жетоны {old} → {new}; старое событие: {before} → {after_old}; "
          f"текущее: → {after_new}")
    return old != new and after_old == before == "cart" and after_new == "reminder"


if __name__ == "__main__":
    engine_path, doc = sys.argv[1], sys.argv[2]
    ok1 = scenario_orders(engine_path, doc)
    ok2 = scenario_stale_timer(engine_path, doc)
    print("--- контроль: хост без отсева дублей и без ключей идемпотентности")
    control = scenario_orders(engine_path, doc, naive=True)
    print("  тест ловит нарушение:", "да" if not control else "НЕТ")
    ok2 = ok2 and not control
    print("контракт выполнен" if ok1 and ok2 else "НАРУШЕНИЕ КОНТРАКТА")
    sys.exit(0 if ok1 and ok2 else 1)

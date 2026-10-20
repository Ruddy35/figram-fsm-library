"""Эталонный хост: контракт обработки событий (HOST.md) и его проверка.

Модель продакшена в миниатюре:
* «база» с атомарными транзакциями: снимок пользователя, обработанные
  update_id, очередь исходящих действий (outbox) с данными события и данные
  хоста (корзина, адрес) пишутся вместе;
* отдельный исполнитель outbox (фоновый поток) выполняет действия с ключами
  идемпотентности; локальные вызовы (корзина, адрес) — по ключу один раз;
* «CRM» принимает вызов один раз на ключ;
* условия вычисляются по настоящим данным: `cart_not_empty` — по корзине,
  `in_delivery_zone` — по тексту сообщения; перед шагом пользователя хост
  дожидается исполнения его незавершённых действий (барьер, HOST.md §1.3);
* планировщик таймеров присылает события с жетонами.

Сбои внедряются случайно: до фиксации транзакции, после фиксации до
исполнения, между исполнением и отметкой «выполнено». Повторная доставка
и параллельная доставка событий одного пользователя — тоже. Пользователи
проходят разные ветки: пустая корзина при «Оформить», адрес вне зоны.

    python3 host_sim.py ENGINE pizza.bot.json [--no-barrier]

`--no-barrier` — хост без барьера (условия читают данные до исполнения
действий прошлого шага): тест должен быть красным.
"""

import copy
import json
import os
import random
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "clients", "python"))
from figram_client import Engine


class Crash(Exception):
    """Внедрённый сбой процесса хоста."""


LOCAL_CALLS = {"add_to_cart", "clear_cart", "save_size", "save_address"}


class DB:
    """Хранилище с атомарными транзакциями (одна блокировка на фиксацию)."""

    def __init__(self):
        self._lock = threading.Lock()
        self.users = {}        # user -> {"snapshot": ..., "seen": set()}
        self.outbox = []       # {"key", "user", "action", "payload", "done"}
        self.data = {}         # user -> {"cart": int, "address": str | None}
        self.applied = set()   # ключи исполненных локальных вызовов (уникальный индекс)

    def read(self, user):
        with self._lock:
            return copy.deepcopy(self.users.get(user, {"snapshot": None, "seen": set()}))

    def get_data(self, user):
        with self._lock:
            return dict(self.data.get(user, {"cart": 0, "address": None}))

    def commit(self, user, record, actions, payload):
        with self._lock:
            self.users[user] = record
            for i, a in enumerate(actions):
                self.outbox.append({"key": f"{user}:{record['snapshot']['seq']}:{i}",
                                    "user": user, "action": a, "payload": payload, "done": False})

    def apply_local(self, key, user, name, payload):
        """Локальный вызов в той же базе: исполняется один раз на ключ."""
        with self._lock:
            if key in self.applied:
                return
            self.applied.add(key)
            d = self.data.setdefault(user, {"cart": 0, "address": None})
            if name == "add_to_cart":
                d["cart"] += 1
            elif name == "clear_cart":
                d["cart"] = 0
            elif name == "save_address":
                d["address"] = payload

    def pending(self, user=None):
        with self._lock:
            return [e for e in self.outbox if not e["done"] and (user is None or e["user"] == user)]

    def mark_done(self, entry):
        with self._lock:
            entry["done"] = True


class CRM:
    """Внешняя система: вызов с тем же ключом выполняется один раз."""

    def __init__(self):
        self._lock = threading.Lock()
        self.executed = {}     # key -> (user, name, data)

    def call(self, key, user, name, data):
        with self._lock:
            self.executed.setdefault(key, (user, name, data))


class Host:
    def __init__(self, engine, crash_rate, naive=False, barrier=True):
        self.naive = naive         # контрольный хост без контракта
        self.barrier = barrier     # HOST §1.3: действия прошлых шагов исполнены до условий
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
        self.order_log = {}    # user -> (событие, жетон, условия) в порядке обработки
        self._stop = threading.Event()
        self._executor = None

    def _maybe_crash(self, where):
        if random.random() < self.crash_rate:
            self.crashes += 1
            raise Crash(where)

    def _user_lock(self, user):
        with self._locks_lock:
            return self._user_locks.setdefault(user, threading.Lock())

    def guards(self, user, payload, used):
        """Условия по данным хоста (корзина) и по данным события (текст адреса)."""
        def f(names):
            d = self.db.get_data(user)
            vals = {"cart_not_empty": d["cart"] > 0,
                    "in_delivery_zone": bool(payload) and payload.startswith("zone:")}
            out = {n: vals.get(n, False) for n in names}
            used.update(out)
            return out
        return f

    def handle(self, user, update_id, event, token=None, payload=None):
        """Обработка одного обновления. Возвращает статус; Crash — сбой."""
        with self._user_lock(user):                     # 1. по одному на пользователя
            if self.barrier:
                self.drain(user)                         # барьер: прошлые действия исполнены
            rec = self.db.read(user)
            if update_id in rec["seen"] and not self.naive:  # 2. повторная доставка
                return "duplicate"
            used = {}
            if rec["snapshot"] is None:
                r = self.engine.start(user)
            else:
                r = self.engine.step(user, rec["snapshot"], event, self.guards(user, payload, used), token)
            self._maybe_crash("до фиксации")             # сбой: ничего не записано
            rec["snapshot"] = r["snapshot"]
            rec["seen"].add(update_id)
            self.db.commit(user, rec, r["actions"], payload)  # 3. атомарно: снимок + outbox
            self.order_log.setdefault(user, []).append((event, token, used))
        self._maybe_crash("после фиксации")               # сбой: действия в outbox
        return "ok"

    def drain(self, user=None):
        """Исполнитель outbox: каждое действие выполняется с ключом идемпотентности."""
        with self._exec_lock:
            for e in self.db.pending(user):
                a = e["action"]
                if "call" in a:
                    key = e["key"] if not self.naive else f"{e['key']}#{random.random()}"
                    if a["call"] in LOCAL_CALLS:
                        self.db.apply_local(key, e["user"], a["call"], e["payload"])
                    else:
                        self.crm.call(key, e["user"], a["call"], self.db.get_data(e["user"]))
                elif "send" in a:
                    self.sent.append((e["key"], e["user"], a["send"]))
                elif "start_timer" in a:
                    self.timers[(e["user"], a["start_timer"])] = a["token"]
                elif "cancel_timer" in a:
                    self.timers.pop((e["user"], a["cancel_timer"]), None)
                self._maybe_crash("до отметки «выполнено»")  # сбой: выполнено, не отмечено
                self.db.mark_done(e)

    def start_executor(self, poll=0.05):
        """Отдельный исполнитель (HOST §2): фоновый поток, опрашивает outbox раз в
        `poll` секунд (как типичный опросчик outbox); сбой = перезапуск."""
        def loop():
            while not self._stop.is_set():
                try:
                    self.drain()
                except Crash:
                    pass
                time.sleep(poll)
        self._executor = threading.Thread(target=loop, daemon=True)
        self._executor.start()

    def stop_executor(self):
        self._stop.set()
        if self._executor:
            self._executor.join()


def deliver(host, user, update_id, event, token=None, payload=None, attempts=50):
    """Доставка «как Telegram»: повторять, пока хост не подтвердит."""
    for _ in range(attempts):
        try:
            return host.handle(user, update_id, event, token, payload)
        except Crash:
            time.sleep(0.001)
    raise RuntimeError("не удалось доставить")


ORDER = ["/start", "btn:order", "btn:pizza", "btn:size", "btn:add",
         "btn:checkout", "msg:text", "payment:success"]
# Сначала «Оформить» с пустой корзиной (ложная ветка → каталог), потом заказ.
EMPTY_FIRST = ["/start", "btn:order", "btn:cart", "btn:checkout", "btn:pizza", "btn:size",
               "btn:add", "btn:checkout", "msg:text", "payment:success"]


def plan(u):
    """События пользователя u и ожидаемый итог: адрес в зоне → done и один заказ,
    вне зоны → pickup и ни одного."""
    events = EMPTY_FIRST if u % 3 == 0 else ORDER
    in_zone = u % 2 == 0
    text = "zone:Ленина 1" if in_zone else "far:Загородная 99"
    seq = [(ev, text if ev == "msg:text" else None) for ev in events]
    return seq, ("done", 1) if in_zone else ("pickup", 0)


def scenario_orders(engine_path, doc, users=30, crash_rate=0.15, naive=False, barrier=True):
    random.seed(1)
    engine = Engine(engine_path, doc, processes=2)
    host = Host(engine, crash_rate, naive, barrier)
    host.start_executor()

    def user_stream(u):
        for i, (ev, payload) in enumerate(plan(u)[0]):
            with ThreadPoolExecutor(2) as pool:           # дубль параллельно
                list(pool.map(lambda _: deliver(host, f"u{u}", i, ev, payload=payload), range(2)))

    with ThreadPoolExecutor(16) as pool:
        list(pool.map(user_stream, range(users)))
    host.stop_executor()
    for _ in range(100):                                  # дожать outbox после сбоев
        try:
            host.drain()
            break
        except Crash:
            pass
    host.crash_rate = 0
    host.drain()

    orders = [v for v in host.crm.executed.values() if v[1] == "create_order"]
    expected_ok = 0
    wrong = []
    for u in range(users):
        exp_at, exp_orders = plan(u)[1]
        text = next(p for ev, p in plan(u)[0] if ev == "msg:text")
        at = host.db.users[f"u{u}"]["snapshot"]["at"]
        mine = [o for o in orders if o[0] == f"u{u}"]
        # Заказ — с непустой корзиной и с адресом из сообщения пользователя.
        good = all(o[2]["cart"] > 0 and o[2]["address"] == text for o in mine)
        if at == exp_at and len(mine) == exp_orders and good:
            expected_ok += 1
        else:
            wrong.append((f"u{u}", at, len(mine), [(o[2]["cart"], o[2]["address"]) for o in mine]))
    pending = len(host.db.pending())
    total = sum(len(plan(u)[0]) for u in range(users))
    # Порядок: снимок каждого пользователя равен последовательному проигрыванию
    # его событий в порядке обработки с теми же условиями — нет потерянных обновлений.
    lost = 0
    for u in range(users):
        snap = None
        for ev, tok, used in host.order_log[f"u{u}"]:
            snap = (engine.start("ref") if snap is None
                    else engine.step("ref", snap, ev, lambda ns, used=used: {n: used[n] for n in ns}, tok))["snapshot"]
        if snap != host.db.users[f"u{u}"]["snapshot"]:
            lost += 1
    engine.close()
    name = "НАИВНЫЙ хост" if naive else ("хост по контракту" if barrier else "хост БЕЗ барьера")
    print(f"{name}, внедрено сбоев {host.crashes}")
    print(f"  пользователей {users}: итог как ожидалось (done/pickup, заказов 1/0, в заказе корзина и адрес) у {expected_ok}, "
          f"заказов в CRM {len(orders)}, обработано событий {sum(len(v) for v in host.order_log.values())} "
          f"из {total} (повторные доставки), потерянных обновлений {lost}, невыполненных действий {pending}")
    if wrong:
        print(f"  не так, как ожидалось: {wrong[:5]}{' …' if len(wrong) > 5 else ''}")
    return expected_ok == users and lost == 0 and pending == 0


def scenario_stale_timer(engine_path, doc):
    engine = Engine(engine_path, doc)
    host = Host(engine, 0)

    def step(i, ev, token=None):
        deliver(host, u, i, ev, token)
        host.drain()

    u = "t"
    step(0, "/start")
    for i, ev in enumerate(["btn:order", "btn:cart"], 1):  # корзина запускает напоминание
        step(i, ev)
    old = host.timers[(u, "cart_reminder")]
    step(3, "btn:cancel")                                   # уход — таймер отменён
    step(4, "btn:order")
    step(5, "btn:cart")                                     # новый запуск, новый жетон
    new = host.timers[(u, "cart_reminder")]
    before = host.db.users[u]["snapshot"]["at"]
    step(6, "timer:cart_reminder", token=old)               # запоздавшее старое событие
    after_old = host.db.users[u]["snapshot"]["at"]
    step(7, "timer:cart_reminder", token=new)
    after_new = host.db.users[u]["snapshot"]["at"]
    engine.close()
    print(f"таймер: жетоны {old} → {new}; старое событие: {before} → {after_old}; "
          f"текущее: → {after_new}")
    return old != new and after_old == before == "cart" and after_new == "reminder"


if __name__ == "__main__":
    engine_path, doc = sys.argv[1], sys.argv[2]
    barrier = "--no-barrier" not in sys.argv[3:]
    ok1 = scenario_orders(engine_path, doc, barrier=barrier)
    ok2 = scenario_stale_timer(engine_path, doc)
    print("--- контроль: хост без отсева дублей и без ключей идемпотентности")
    # Контроль вероятностный (дубль заказа возникает только при сбое в нужном месте):
    # до трёх попыток, достаточно одной красной. Основной сценарий не повторяется.
    for attempt in range(3):
        control = scenario_orders(engine_path, doc, naive=True)
        if not control:
            break
    print("  тест ловит нарушение:", "да" if not control else "НЕТ", f"(попыток контроля: {attempt + 1})")
    ok2 = ok2 and not control
    print("контракт выполнен" if ok1 and ok2 else "НАРУШЕНИЕ КОНТРАКТА")
    sys.exit(0 if ok1 and ok2 else 1)

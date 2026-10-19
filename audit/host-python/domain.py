"""Domain model of the four test bots: guards are computed from the host's domain tables,
calls mutate them (local calls) or go to the simulated CRM (external calls).
The normative description is /home/user/figram-audit/hosts/shared/domain.md.

Guard functions get (conn, user, upd) where upd = {"arg": str|None, ...} is the update being
processed. They are evaluated AFTER the host drained the user's own pending calls (rule G1) and,
for shared scarce resources, under the resource lock (rule G2).
"""
from __future__ import annotations

import json
import re

ZONES = ("Lenina", "Mira", "Pushkina", "Gagarina")
SLOTS = {"s1": 1, "s2": 2, "s3": 4, "s4": 10}

SCHEMA = """
CREATE TABLE IF NOT EXISTS zones(street TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS prefs(user TEXT PRIMARY KEY, size TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS cart(key TEXT PRIMARY KEY, user TEXT NOT NULL, size TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS addresses(user TEXT PRIMARY KEY, address TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS orders_local(key TEXT PRIMARY KEY, user TEXT NOT NULL, order_id INTEGER NOT NULL,
  items TEXT NOT NULL, address TEXT);
CREATE TABLE IF NOT EXISTS slots(slot TEXT PRIMARY KEY, capacity INTEGER NOT NULL CHECK(capacity>=0));
CREATE TABLE IF NOT EXISTS selection(user TEXT PRIMARY KEY, slot TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS reservations(key TEXT PRIMARY KEY, user TEXT NOT NULL, slot TEXT NOT NULL,
  active INTEGER NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS reservations_user_slot ON reservations(user, slot) WHERE active=1;
CREATE TABLE IF NOT EXISTS calendar_local(key TEXT PRIMARY KEY, user TEXT NOT NULL, event_id INTEGER);
CREATE TABLE IF NOT EXISTS tickets(user TEXT PRIMARY KEY, ticket_id INTEGER NOT NULL, state TEXT NOT NULL,
  messages INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS reminders(user TEXT PRIMARY KEY, text TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS pings(key TEXT PRIMARY KEY, user TEXT NOT NULL, text TEXT);
CREATE TABLE IF NOT EXISTS notifications_local(key TEXT PRIMARY KEY, user TEXT NOT NULL);
"""


def seed(conn):
    conn.executemany("INSERT OR IGNORE INTO zones(street) VALUES(?)", [(z,) for z in ZONES])
    conn.executemany("INSERT OR IGNORE INTO slots(slot, capacity) VALUES(?,?)", list(SLOTS.items()))


class PermanentCallError(Exception):
    pass


def street_of(arg) -> str:
    if not isinstance(arg, str):
        return ""
    m = re.match(r"\s*([^\s,]+)", arg)
    return m.group(1) if m else ""


# ------------------------------------------------------------------ guards
def g_cart_not_empty(c, user, upd):
    return c.execute("SELECT COUNT(*) FROM cart WHERE user=?", (user,)).fetchone()[0] > 0


def g_in_delivery_zone(c, user, upd):
    return c.execute("SELECT 1 FROM zones WHERE street=?", (street_of(upd.get("arg")),)).fetchone() is not None


def selected_slot(c, user):
    r = c.execute("SELECT slot FROM selection WHERE user=?", (user,)).fetchone()
    return r[0] if r else None


def g_slot_free(c, user, upd):
    slot = selected_slot(c, user)
    if slot is None:
        return False
    cap = c.execute("SELECT capacity FROM slots WHERE slot=?", (slot,)).fetchone()
    if cap is None:
        return False
    active = c.execute("SELECT COUNT(*) FROM reservations WHERE slot=? AND active=1", (slot,)).fetchone()[0]
    mine = c.execute("SELECT COUNT(*) FROM reservations WHERE slot=? AND active=1 AND user=?", (slot, user)).fetchone()[0]
    # committed-but-not-yet-executed reservation intents of OTHER users (rule G2)
    pending = c.execute("SELECT COUNT(*) FROM outbox WHERE state='pending' AND kind='call' AND name='reserve_slot' "
                        "AND param=? AND user<>?", (slot, user)).fetchone()[0]
    return mine == 0 and active + pending < cap[0]


def g_has_booking(c, user, upd):
    return c.execute("SELECT COUNT(*) FROM reservations WHERE user=? AND active=1", (user,)).fetchone()[0] > 0


def _ticket(c, user):
    return c.execute("SELECT ticket_id, state FROM tickets WHERE user=?", (user,)).fetchone()


def g_can_escalate(c, user, upd):
    t = _ticket(c, user)
    return t is not None and t[1] == "open"


def g_ticket_open(c, user, upd):
    t = _ticket(c, user)
    return t is not None and t[1] in ("open", "escalated")


def _reminder(c, user):
    r = c.execute("SELECT text FROM reminders WHERE user=?", (user,)).fetchone()
    return r[0] if r else None


def g_has_reminder(c, user, upd):
    t = _reminder(c, user)
    return t is not None and t != ""


def g_nag_enabled(c, user, upd):
    t = _reminder(c, user)
    return t is not None and t.startswith("!")


GUARDS = {
    "cart_not_empty": g_cart_not_empty,
    "in_delivery_zone": g_in_delivery_zone,
    "slot_free": g_slot_free,
    "has_booking": g_has_booking,
    "can_escalate": g_can_escalate,
    "ticket_open": g_ticket_open,
    "has_reminder": g_has_reminder,
    "nag_enabled": g_nag_enabled,
}


def guard_resources(name, c, user):
    """Rule G2: guards over shared scarce resources are evaluated under a resource lock that is
    held until the step's transaction commits (so the reservation intent becomes visible)."""
    if name == "slot_free":
        return [f"slot:{selected_slot(c, user)}"]
    return []


# ------------------------------------------------------------------ calls
# param: how the call's parameter is fixed at COMMIT time ("arg" = text of the triggering update,
# "selection" = currently selected slot, None = nothing).
class Call:
    def __init__(self, name, kind, param=None, op=None):
        self.name, self.kind, self.param, self.op = name, kind, param, op


CALLS = {c.name: c for c in [
    Call("clear_cart", "local"),
    Call("save_size", "local", "arg"),
    Call("add_to_cart", "local"),
    Call("save_address", "local", "arg"),
    Call("create_order", "external", op="create_order"),
    Call("choose_slot", "local", "arg"),
    Call("reserve_slot", "local", "selection"),
    Call("notify_calendar", "external", "selection", op="calendar"),
    Call("release_hold", "local"),
    Call("cancel_booking", "local"),
    Call("create_ticket", "external", "arg", op="ticket_create"),
    Call("log_message", "external", "arg", op="ticket_message"),
    Call("escalate", "external", op="ticket_escalate"),
    Call("close_ticket", "external", op="ticket_close"),
    Call("save_reminder", "local", "arg"),
    Call("clear_reminder", "local"),
    Call("log_ping", "local"),
    Call("notify_owner", "external", op="notify"),
]}


def resolve_param(call: Call, c, user, upd):
    if call.param == "arg":
        a = upd.get("arg")
        return a if isinstance(a, str) else ""
    if call.param == "selection":
        return selected_slot(c, user)
    return None


def apply_local(c, name, user, key, param, check_capacity=True):
    """Executed inside the executor's transaction together with marking the outbox row done."""
    if name == "clear_cart":
        c.execute("DELETE FROM cart WHERE user=?", (user,))
    elif name == "save_size":
        c.execute("INSERT INTO prefs(user,size) VALUES(?,?) ON CONFLICT(user) DO UPDATE SET size=excluded.size",
                  (user, param or "M"))
    elif name == "add_to_cart":
        r = c.execute("SELECT size FROM prefs WHERE user=?", (user,)).fetchone()
        c.execute("INSERT INTO cart(key,user,size) VALUES(?,?,?) ON CONFLICT(key) DO NOTHING",
                  (key, user, r[0] if r else "M"))
    elif name == "save_address":
        c.execute("INSERT INTO addresses(user,address) VALUES(?,?) "
                  "ON CONFLICT(user) DO UPDATE SET address=excluded.address", (user, param or ""))
    elif name == "choose_slot":
        c.execute("INSERT INTO selection(user,slot) VALUES(?,?) ON CONFLICT(user) DO UPDATE SET slot=excluded.slot",
                  (user, param or ""))
    elif name == "reserve_slot":
        cap = c.execute("SELECT capacity FROM slots WHERE slot=?", (param,)).fetchone()
        active = c.execute("SELECT COUNT(*) FROM reservations WHERE slot=? AND active=1", (param,)).fetchone()[0]
        if check_capacity and (cap is None or active >= cap[0]):
            raise PermanentCallError(f"slot {param} is full ({active}/{cap[0] if cap else 0})")
        try:
            c.execute("INSERT INTO reservations(key,user,slot,active) VALUES(?,?,?,1) ON CONFLICT(key) DO NOTHING",
                      (key, user, param))
        except Exception as e:  # sqlite3.IntegrityError: user already holds this slot
            raise PermanentCallError(f"duplicate reservation: {e}")
    elif name == "release_hold":
        c.execute("DELETE FROM selection WHERE user=?", (user,))
    elif name == "cancel_booking":
        c.execute("UPDATE reservations SET active=0 WHERE user=? AND active=1", (user,))
    elif name == "save_reminder":
        if param:
            c.execute("INSERT INTO reminders(user,text) VALUES(?,?) ON CONFLICT(user) DO UPDATE SET text=excluded.text",
                      (user, param))
        else:
            c.execute("DELETE FROM reminders WHERE user=?", (user,))
    elif name == "clear_reminder":
        c.execute("DELETE FROM reminders WHERE user=?", (user,))
    elif name == "log_ping":
        c.execute("INSERT INTO pings(key,user,text) VALUES(?,?,?) ON CONFLICT(key) DO NOTHING",
                  (key, user, _reminder(c, user)))
    else:
        raise PermanentCallError(f"unknown local call {name}")


def external_payload(c, name, user, param):
    """Payload of an external call, read from the domain DB at execution time (deterministic:
    the user's earlier outbox rows have all been executed before this one)."""
    if name == "create_order":
        items = [r[0] for r in c.execute("SELECT size FROM cart WHERE user=? ORDER BY key", (user,))]
        a = c.execute("SELECT address FROM addresses WHERE user=?", (user,)).fetchone()
        return {"items": items, "address": a[0] if a else None}
    if name == "notify_calendar":
        return {"slot": param}
    if name in ("create_ticket", "log_message"):
        t = _ticket(c, user)
        return {"text": param, "ticket_id": t[0] if (t and name == "log_message") else None}
    if name in ("escalate", "close_ticket"):
        t = _ticket(c, user)
        return {"ticket_id": t[0] if t else None}
    if name == "notify_owner":
        return {"text": _reminder(c, user) or ""}
    raise PermanentCallError(f"unknown external call {name}")


def apply_external_result(c, name, user, key, param, payload, result):
    """Local mirror of an external effect, committed with marking the outbox row done."""
    if name == "create_order":
        c.execute("INSERT INTO orders_local(key,user,order_id,items,address) VALUES(?,?,?,?,?) "
                  "ON CONFLICT(key) DO NOTHING",
                  (key, user, result["order_id"], json.dumps(payload["items"]), payload["address"]))
        c.execute("DELETE FROM cart WHERE user=?", (user,))
    elif name == "notify_calendar":
        c.execute("INSERT INTO calendar_local(key,user,event_id) VALUES(?,?,?) ON CONFLICT(key) DO NOTHING",
                  (key, user, result.get("event_id")))
    elif name == "create_ticket":
        c.execute("INSERT INTO tickets(user,ticket_id,state,messages) VALUES(?,?,'open',1) "
                  "ON CONFLICT(user) DO UPDATE SET ticket_id=excluded.ticket_id, state='open', messages=1",
                  (user, result["ticket_id"]))
    elif name == "log_message":
        c.execute("UPDATE tickets SET messages=messages+1 WHERE user=?", (user,))
    elif name == "escalate":
        c.execute("UPDATE tickets SET state='escalated' WHERE user=?", (user,))
    elif name == "close_ticket":
        c.execute("UPDATE tickets SET state='closed' WHERE user=?", (user,))
    elif name == "notify_owner":
        c.execute("INSERT INTO notifications_local(key,user) VALUES(?,?) ON CONFLICT(key) DO NOTHING", (key, user))


def reset_user_data(c, family, user):
    """'Delete my data, keep the account': session data only; business records stay."""
    if family == "pizza":
        for t in ("cart", "prefs", "addresses"):
            c.execute(f"DELETE FROM {t} WHERE user=?", (user,))
    elif family == "booking":
        c.execute("DELETE FROM selection WHERE user=?", (user,))
    elif family == "reminders":
        c.execute("DELETE FROM reminders WHERE user=?", (user,))


DOMAIN_DIGEST_QUERIES = [
    ("prefs", "SELECT user,size FROM prefs ORDER BY user"),
    ("cart", "SELECT user,size,key FROM cart ORDER BY user,key"),
    ("addresses", "SELECT user,address FROM addresses ORDER BY user"),
    ("orders_local", "SELECT key,user,items,address FROM orders_local ORDER BY key"),
    ("selection", "SELECT user,slot FROM selection ORDER BY user"),
    ("reservations", "SELECT key,user,slot,active FROM reservations ORDER BY key"),
    ("calendar_local", "SELECT key,user FROM calendar_local ORDER BY key"),
    ("tickets", "SELECT user,state,messages FROM tickets ORDER BY user"),
    ("reminders", "SELECT user,text FROM reminders ORDER BY user"),
    ("pings", "SELECT key,user,text FROM pings ORDER BY key"),
    ("notifications_local", "SELECT key,user FROM notifications_local ORDER BY key"),
]

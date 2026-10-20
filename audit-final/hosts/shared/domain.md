# Domain model of the shared test bots (normative for both hosts)

Owner: host-python agent. Reference implementation: `audit/host-python/domain.py` (branch
`audit/host-python`); independent oracle used by the checker: `audit/host-python/checker.py` (`Model`).

Every guard is computed **from the host's domain database at step time** (plus, where stated, the
text/argument of the update being processed). No guard is ever a constant.

## 0. Common rules

### Update argument
An update may carry `"arg"` (string, ≤ 4096 chars): the message text for `msg:*`, the callback
payload for `btn:*` (e.g. the slot id, the reminder text, the pizza size). Calls whose parameter is
`arg` take the `arg` of the update whose transition emitted the call.

### Parameters are fixed at commit time
A call's parameter is resolved inside the step transaction and stored in the outbox row
(`param`): `arg` → the update's arg (missing arg = `""`); `selection` → the user's currently
selected slot (table `selection`). Everything else is read at execution time from the domain DB.

### Rule G1 — guards that depend on the user's own not-yet-executed calls
Before computing guards for user U (i.e. when the engine answered `need`), the host executes U's
pending outbox prefix **up to and including U's last pending `call`**, in outbox order, synchronously
under U's lock. If one of those rows fails transiently (500/429/timeout/DB busy), the step is aborted:
nothing is committed, the update is NOT acknowledged and will be redelivered. Same rule before a
commit that resolves a `selection` parameter. Consequence: a guard always sees all effects of every
earlier committed transition of the same user ("read your own writes").

### Rule G2 — guards over shared scarce resources (only `slot_free`)
`slot_free` is evaluated under a per-slot lock that is held until the step transaction commits, and it
counts committed-but-not-yet-executed `reserve_slot` outbox rows of other users for that slot as
taken. So two users can never both see the last unit as free, and an executed `reserve_slot` can never
fail for capacity. Pending `cancel_booking` rows of other users are NOT counted as freed capacity
(conservative). The corpus puts a `{"ctl":"barrier"}` after cancellations before others compete, so
the result is deterministic.

### Local vs external calls
* local: applied to the host's domain DB **in the same transaction** that marks the outbox row done
  → exactly once.
* external: sent to the simulated CRM with the idempotency key; the CRM stores the result per key
  (Stripe-like) → effectively once; the host then mirrors the result into the domain DB in the same
  transaction that marks the row done.
* `send`: Telegram — no idempotency key exists → **at-least-once** (duplicates possible after
  timeouts, network duplicates, a crash or DB failure between delivery and marking the row done).

## 1. pizza (`pizza`, `pizza.v2`)

Seed data: `zones(street)` = `Lenina`, `Mira`, `Pushkina`, `Gagarina` (case-sensitive).

| guard | definition |
|---|---|
| `cart_not_empty` | `SELECT COUNT(*) FROM cart WHERE user=U` > 0 |
| `in_delivery_zone` | first token of the update's `arg` (split on whitespace/comma) ∈ `zones`; no arg → false |

| call | kind | effect |
|---|---|---|
| `clear_cart` | local | `DELETE FROM cart WHERE user=U` |
| `save_size` | local, param `arg` | upsert `prefs(user, size=arg or "M")` |
| `add_to_cart` | local | insert `cart(key=<idempotency key>, user, size=prefs.size or "M")`, ignore if key exists |
| `save_address` | local, param `arg` | upsert `addresses(user, address=arg)` |
| `create_order` | external `create_order` | payload `{"items":[cart sizes ordered by key], "address": addresses.address}`; CRM inserts `crm_orders(key UNIQUE)`, returns `order_id`; mirror: insert `orders_local(key)`, then `DELETE FROM cart WHERE user=U` |

## 2. booking (`booking`)

Seed data: `slots(slot, capacity)` = `s1:1`, `s2:2`, `s3:4`, `s4:10`.

| guard | definition |
|---|---|
| `slot_free` | s = selection(U); false if none/unknown slot; else `active(s) + pending_reserve_intents(s, other users) < capacity(s)` AND U holds no active reservation on s (rule G2) |
| `has_booking` | U has ≥ 1 row in `reservations` with `active=1` |

| call | kind | effect |
|---|---|---|
| `choose_slot` | local, param `arg` | upsert `selection(user, slot=arg)` |
| `reserve_slot` | local, param `selection` | if active(slot) < capacity and U has no active reservation on slot: insert `reservations(key, user, slot, active=1)` (unique partial index `(user,slot) WHERE active=1`); otherwise permanent failure (outbox row `failed`) — never happens under G2 |
| `notify_calendar` | external `calendar`, param `selection` | CRM inserts `crm_calendar(key UNIQUE, slot)`; mirror `calendar_local(key)` |
| `release_hold` | local | `DELETE FROM selection WHERE user=U` |
| `cancel_booking` | local | `UPDATE reservations SET active=0 WHERE user=U AND active=1` |

## 3. support (`support`, `support.v2`)

| guard | definition |
|---|---|
| `can_escalate` | `tickets(U).state = 'open'` |
| `ticket_open` | `tickets(U).state IN ('open','escalated')` |

All calls are external (helpdesk = simulated CRM). Mirror table `tickets(user PK, ticket_id, state, messages)`.

| call | CRM op | payload | mirror |
|---|---|---|---|
| `create_ticket` (param `arg`) | `ticket_create` | `{"text": arg}` | upsert `tickets(U, ticket_id, 'open', messages=1)` |
| `log_message` (param `arg`) | `ticket_message` | `{"ticket_id", "text": arg}` | `messages += 1` |
| `escalate` | `ticket_escalate` | `{"ticket_id"}` | `state='escalated'` |
| `close_ticket` | `ticket_close` | `{"ticket_id"}` | `state='closed'` |

A `ticket_*` op on a missing ticket is a permanent CRM error (never happens with these bots).

## 4. reminders (`reminders`, `reminders.v2`)

| guard | definition |
|---|---|
| `has_reminder` | `reminders(U).text` exists and ≠ "" |
| `nag_enabled` | `reminders(U).text` starts with `!` |

| call | kind | effect |
|---|---|---|
| `save_reminder` | local, param `arg` | arg non-empty → upsert `reminders(U, text=arg)`; empty → delete |
| `clear_reminder` | local | delete `reminders(U)` |
| `log_ping` | local | insert `pings(key, user, text=current reminder text)` |
| `notify_owner` | external `notify` | payload `{"text": current reminder text or ""}`; CRM inserts `crm_notify(key UNIQUE)`; mirror `notifications_local(key)` |

## 5. Reset ("delete my data, keep the account", corpus `reset_user`)

Clears session data only: pizza → `cart`, `prefs`, `addresses`; booking → `selection`;
reminders → `reminders`; support → nothing. Business records (orders, reservations, tickets) stay.
The FSM side must use `restart` with the user's high-water `seq` (never `start`, see ET-01) and cancel
every scheduled timer of the user.

## 6. Snapshot repair (corpus `corrupt_snapshot`)

A stored snapshot that is not valid per `snapshot.schema.json`, or whose `seq` differs from the
host's persistent high-water `seq` of that user (tampered, restored from an old backup), is
replaced by `restart` with `{"at":"-","seq":<high-water>}`; all scheduled timers of the user are
canceled; domain data is kept; the update that discovered it is consumed (acknowledged) by the repair.
A valid snapshot whose `at` no longer exists is NOT a repair: the engine answers `recovered:true`.

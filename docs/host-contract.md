# Host contract

The engine is a pure function: a snapshot and an event go in, a new snapshot and actions come
out. Everything that involves storage, the network and time is done by the **host** — the
developer's code around the engine. This document lists the rules under which a bot behaves as
the designer drew it, even with failures, redeliveries and concurrent load. The protocol itself
is specified in [format.md](format.md), section 8.

The reference implementation of these rules and its test is
[`tests/engine/host_sim.py`](../tests/engine/host_sim.py) (section 7).

## 1. Processing an event

For every Telegram update (it carries an `update_id`):

1. **One at a time per user.** Events of one user are processed strictly sequentially: a lock
   or a queue per user. Different users are processed in parallel. Without this, two events
   read the same snapshot and one of the updates is lost.
2. **Duplicate filtering.** If the `update_id` was already processed for this user, the event
   is skipped. Processed `update_id`s are recorded in the same transaction as the snapshot
   (step 4), in a separate indexed table "(user, `update_id`)", not as a set inside the snapshot
   row (that set would grow with every event). It is enough to keep the redelivery window:
   Telegram does not keep undelivered updates longer than 24 hours (Bot API documentation).
   Keep the mark `timer:<name>:<token>` of a timer event while the user's snapshot still holds
   the timer with that token (section 3: a firing accepted without a transition leaves the timer
   in the snapshot, and a redelivery would be accepted again; `seq` passing the token is not
   enough, an arrow without a target raises `seq` and keeps the timer).
3. **Step.** The host reads the snapshot and calls `step`. If the engine answers `need`, the
   host computes the conditions and repeats `step` with `guards`. Conditions are computed on
   data where **all sequential `call` actions committed by earlier steps of this user are
   already applied** (and the actions of the current step are not). Actions are executed by a
   separate executor (section 2), so before a step wait until the user's pending outbox actions
   are executed (a per-user barrier) — or execute the calls that change data read by conditions
   in the same transaction as the snapshot. Background actions (`"detached": true`, section 2)
   are not part of the barrier. Otherwise `cart_not_empty` after "Add" reads an empty
   cart, and a balance check allows two purchases with the same money. Conditions may also
   depend on the data of the event itself (message text, button payload).

   **A new user** (no snapshot) comes with an update too: `/start ref123`, a button of an old
   message after the snapshot was lost, a text. Call `start` with the update's event (and `seq`,
   the high-water mark of section 4, if the user had one) instead of `step`; `need` is
   answered the same way. The event is matched as if the user stood on the initial message
   ([format.md](format.md), section 7, decision 9), so one reply both starts the user and
   processes the update, and the document decides what `/start` does. The engine never sees
   the parameter `ref123`: a condition (`ref_valid`) and an action (`save_ref`) of the host read
   it from the update. A timer event or an outcome event for a user without a snapshot is not
   such an update: a new user has no timers and no waits, the event belongs to a lost or deleted
   snapshot, and `start` rejects it (`bad_request`). Drop it and record its mark as processed
   (`timer:<name>:<token>`, `call:<action>:<token>`, with the run id if the snapshot database
   can roll back, step 4); do not start the user with it.

   **Shared scarce resources.** The per-user barrier does not protect data shared between
   users: the last seat, the remaining stock, a limit. Two users checking `slot_free` at the
   same time both see a free seat and both get "booked", and one of the reservation actions
   fails later. For such a condition, hold a lock on the resource (for example
   `SELECT … FOR UPDATE` on the seat row) from computing the condition until the step's
   transaction commits, and count as taken also the reservations of other users that are
   committed but not yet executed (outbox rows); do not count cancellations that are not yet
   executed as free. Or reserve the resource directly in the step's transaction. Lock several
   resources in one fixed order, after the user's lock.
4. **Atomic write.** One transaction stores: the new snapshot (the JSON the engine returned,
   unchanged — a host that maps it to a record type of its own drops the fields it does not
   know, such as `back` added in 0.2.0, and "back" then always takes its fallback, or `pending`
   added in 0.3.0, and the outcomes of the calls are then ignored; store it even when `seq` did
   not change: an accepted outcome without an arrow removes its wait without a transition), the mark
   "`update_id` processed", and the reply's actions in an outbox. An outbox row stores the `seq` of the new
   snapshot, the action's position in the reply (execution follows them, section 2) and an
   idempotency key. The key must name the logical action uniquely and must never be given to
   another action — including after a rollback of the snapshot database (failover to an
   asynchronous replica that lost the last transactions, restore from a backup). The
   recommended key is derived from the delivery that caused the step:
   `user:update_id:action:occurrence`. Here `update_id` is unique within its source and never
   reissued: for Telegram `tg:<update_id>`; for a timer `timer:<name>:<token>:<run id>`, where
   the run id is a random UUID that the host writes into the `start_timer` row at commit and
   keeps with the scheduled event (it is not sent to the engine — the engine rejects unknown
   request fields); for an outcome `call:<action>:<token>:<run id>`, the run id written into the
   outbox row of the call at commit and kept with its outcome (after a rollback the reissued
   `seq` gives a new call the same token, and the late outcome of the lost call would otherwise
   answer it: drop an outcome whose run id is not that of the current call with its token); for
   a reset (section 4) — the id of the reset operation. `action` is
   `call:create_order`, `send:menu`, `start_timer:cart_reminder` and so on; `occurrence` is the
   index of the same action within this reply (usually 0). After a rollback, a redelivery of
   the same update gets the same key, so the external system does not execute the action
   twice, and a different event that takes over the freed `seq` gets a different key and is
   not dropped as a duplicate.

   The key `user:seq:index` (`seq` of the new snapshot, index — the action's position in the
   reply) is simpler and safe only while a user's `seq` never repeats: an existing user is never
   started over with `start` without `seq` (section 4), `restart` only ever gets the latest
   committed snapshot, and the snapshot database is never rolled back relative to external
   systems (synchronous replication, no restore from backup). `host_sim.py` works this way.
   After a rollback such a key is given to an action of another branch: one action is dropped
   as a duplicate (an order is lost), and a redelivered `payment:success` gets a new `seq` and
   executes again (a second order). If you need keys based on `seq` and rollbacks are possible,
   add an epoch: `user:epoch:seq:index`, where the epoch (global or per user) is stored outside
   the database that can roll back and is incremented on every restore and failover. The
   epoch prevents lost actions, but after a rollback a redelivery of the same update executes
   its actions again.
5. **Acknowledgement.** Acknowledge the update to Telegram only after the transaction commits.
   A failure before the commit makes Telegram deliver the update again, and it is processed as
   the first time. Webhook: the acknowledgement is a 2xx response to the request carrying the
   update. getUpdates: the acknowledgement is cumulative — a call with `offset` confirms **all**
   updates with a smaller `update_id`, including unprocessed updates of other users; so first
   write the received updates into an inbound queue in the database and only then request the
   next `offset` (process from the queue), or confirm only up to the smallest unfinished
   `update_id`.

## 2. Executing actions

A separate executor takes actions from the outbox in order and marks them executed.

* **Idempotency.** An action may be executed twice (a failure between execution and the mark),
  so external calls pass the action's key as the idempotency key: CRM, payment service, the
  host's own database with a unique index. This way `create_order` runs once no matter how
  often the executor restarts.
* **Order.** Actions of one user run strictly in commit order — by `seq`, and within a reply by
  position (timer cancellations, the variant's actions, timer starts, sending the message) —
  including actions of different replies. Actions of different users are independent: use
  several executors only with the queue partitioned by user. Otherwise a timer start of an
  earlier step can run after a cancellation or a newer start, and a reminder is lost (the rules
  of section 3 protect timers even when the order is broken).
* **Event data.** If an action needs data from the event (`save_address` needs the message
  text), store the data with the action in the same transaction.
* **Failures.** A permanent failure of an external system must not stop execution for other
  users: after a few attempts the action goes to a separate failure queue with an alert, and
  that user's queue waits for a decision. An action with outcomes does not stop the user: its
  failure is the outcome `failed` (below), and the queue goes on. A background action does not
  stop the user either; a background action with outcomes that fails for good also delivers
  `failed`, so its wait is answered.
* **Background actions** (`{"call": …, "detached": true}`, [format.md](format.md), section 7,
  decision 11) are not waited for: the barrier of section 1, step 3 does not include them, and
  the executor may run them from a separate queue, in any order relative to the user's other
  actions, after the commit that produced them. After a few failed attempts such an action goes
  to the failure queue with an alert, and the user goes on; if it has outcomes, the host also
  delivers `failed` (below). Declare an action as a background
  action only if no condition reads its effect (a condition computed before it ran sees the old
  data) and no other action needs it to have run first — a notification, a log entry, an
  analytics event. It may run twice like any action, so it takes the idempotency key too.
* **Outcomes.** A `call` with a `token` has outcomes ([format.md](format.md), section 7,
  decision 12). After performing the action, the host delivers exactly one event
  `call:<action>:<outcome>` with that token: a declared outcome whenever the external system
  answered, a negative answer included (`rejected` with the reason is an outcome, not a
  failure), or `failed` when the host gives up without an answer (no answer, a server error,
  retries exhausted). No answer does not mean that the action was not performed: before
  delivering `failed` for an action that may have taken effect, reconcile — ask the external
  system for the result of the call by its idempotency key (section 1, step 4) and deliver
  that outcome if it acted. Otherwise a retry that the designer draws after `failed` (the
  pizza's `order_failed` → `btn:retry`) is a new call with a new key and creates a second
  order; if reconciling is impossible, make such a retry safe with a business key (the id of
  the order the host keeps), not the key of the call. Deliver only a declared outcome or
  `failed`: the engine does not know the declarations at run time and accepts any outcome name
  with the token of a wait; an undeclared one has no arrow anywhere (`figram check` rejects such
  arrows), so it only removes the wait, and a user waiting on "please wait" stays there. The
  outcome is an event of the user and goes through section 1: one at a time per user, in order
  with the user's other events, duplicate filtering with the `update_id`
  `call:<action>:<token>` (with the run id if the database can roll back, section 1, step 4),
  the atomic write. Record it in the same transaction that marks the action executed (or record it first
  when the action runs outside the transaction), so that a failure cannot lose it: a wait whose
  outcome never comes stays in the snapshot — with `every` for good — and a user waiting for it
  on a "please wait" message waits forever. Store the details of the outcome (the error of the
  external system) with the event: conditions and message texts read them. An outcome is handled
  by the arrows of the message the user is on when it is processed: a sequential action runs
  before the user's next steps are computed (the barrier), but its outcome event arrives later
  and may come after other events of the user — the user pressed "Cancel" on "please wait" —
  and a background action with outcomes may answer at any time. On a message without an arrow
  for it the outcome only answers the call; draw the arrows of an outcome that must not be lost
  on a section or the root, where they work on every message inside. With `every`, each call
  adds a wait until its outcome comes: a host that does not answer them makes the snapshot
  grow until requests with it exceed the line limit (`too_large`).
* **Timer deadlines** are counted from the step's commit (store the time in the outbox), not
  from the moment the executor reaches `start_timer`.
* **send** — sending a message to the user, "at least once". A resend after a failure is
  visible to the user; storing the `message_id` under the action's key narrows the window but
  does not close it: a failure between Telegram's reply and writing the `message_id` still
  produces a duplicate.

## 3. Timers

* `start_timer` comes with a token. The host schedules the event
  `{"event": "timer:<name>", "token": <token>}` after `ms` milliseconds and stores it under the
  pair (user, name). A start replaces the stored event with the same name only if its token is
  greater than the stored one.
* `cancel_timer` carries no token. Remove a scheduled event only if its token is smaller than
  the `seq` of the reply that carried the cancellation (`seq` is stored in the outbox row,
  section 1): a start in the same reply has a token equal to that `seq`, later starts have
  greater tokens, so a cancellation never removes a newer start, even if the executor runs
  actions out of order. Alternatively the scheduler stores events by the triple
  (user, name, token), and a cancellation removes the events with that name and a token smaller
  than `seq`.
* If an event is already in flight and cannot be cancelled, that is fine: the engine accepts a
  firing only with the token of the current run and only on the message that owns the timer,
  and ignores a stale one. A missed or late cancellation therefore does not break correctness —
  provided the rule above is followed. A cancellation without that rule, executed after a newer
  start, removes the new timer, and the reminder is lost.
* **Timer events come only from the scheduler** and outcome events only from the executor. The
  host builds events from Telegram updates itself, with its own prefixes (`btn:`, `msg:`,
  `cmd:`, `payment:`), and never passes the engine a `timer:…` or `call:…` event or a `token`
  field taken from user input (button payload, text, deep-link parameter): reject a payload
  that would produce such a name. A token is not a
  secret: it equals the `seq` of a transition and is easy to guess; the engine checks that the
  token is current, not who sent the event. A forged firing is accepted and executes the timer
  arrow's actions, including `call`.
* A timer event goes through the same path as a Telegram update (section 1); use
  `timer:<name>:<token>` as its `update_id`, and `timer:<name>:<token>:<run id>` if the snapshot
  database can roll back (section 1, step 4). An event whose run id differs from the id stored
  in the database for the current run of this timer comes from a rolled-back history — drop
  it: otherwise, with an equal token, it fires early.
* **Firing without a transition.** If the message has no arrow for this timer, or all
  conditions of its variants are false, the engine accepts the firing but the snapshot does not
  change: the timer's entry with the same token stays in `timers` until the user leaves the
  message, and leaving emits `cancel_timer` for it although the event was already delivered. A
  redelivery of the same event is accepted again and, with other condition values, makes a
  transition; therefore keep the mark `timer:<name>:<token>` while the user's snapshot still
  holds the timer with that token (section 1, step 2) — not only until `seq` passes the token:
  an arrow without a target (format.md, section 7, decision 13) on another event raises `seq`
  and leaves the timer in place. A firing handled by an arrow without a target removes the
  timer from the snapshot; leaving the message later still emits `cancel_timer` for it (the
  exit cancels every timer of the message), which then finds no scheduled event. For the same reason `snapshot.timers` is not a list of
  scheduled events: rebuilding the scheduler from it brings back timers that already fired. If
  a conditional reminder must not be lost, give the timer arrow a last variant without a
  condition.
* **A loop restarts all timers of the message.** An arrow to the same message (including a
  timer loop `timer:tick` → the same message) leaves the message and enters it again
  ([format.md](format.md), section 7, decision 3): all timers of the message are cancelled and
  started again, and the message is sent again. A `tick` loop every second on a message with a
  10-second `timeout` timer restarts `timeout` on every tick, and it never fires. Do not put a
  timer loop and a timer that must outlive it on the same message. Make the deadline of a
  periodic message a condition on the tick arrow —
  `"timer:tick": [{"if": "deadline_passed", "to": "timeout"}, {"to": "waiting"}]`, where the
  host computes `deadline_passed` from a time recorded by a `call` action on the arrow that
  leads into the message. `figram check` warns about such loops ([format.md](format.md),
  section 3, "Warnings").

## 4. Resetting a user

To start a user over (a support command, "delete my data" while keeping the account), the `seq`
counter must continue, not restart from zero. Use `restart` with the **latest committed**
snapshot (read under the user's lock, section 1), not `start` without `seq`. `restart` cancels
the old timers, clears the remembered places, the place to go back to and the waits for
outcomes (outcomes of earlier actions are then ignored), and continues `seq`,
so the tokens of new timers do not match the tokens of late old events, and the keys
`user:seq:index` of new actions do not match keys of executed ones.

A snapshot from a lagging replica or a backup is not suitable for `restart`: the engine does
not cancel timers started after that snapshot and reissues `seq` values that were already used.
A late event of such a timer can match a new token and be accepted, and new actions get the
keys of old ones and are dropped as duplicates.

`start` without `seq` (counter from zero) is only for a user who never had an action. After it
the keys `user:1:0`, `user:2:0`, … coincide with keys of past actions: a CRM, a payment service
or an outbox with a unique key silently drops the new actions as duplicates — for example
`create_order` and `save_address` of a second order along the same path are not executed,
although the user sees "order accepted". Keys based on `update_id` (section 1) save the actions
but not the timers: tokens repeat, and a late timer event from the previous life is accepted
early.

If the snapshot is lost or deleted, keep a separate "high-water mark" — the largest `seq` ever
issued to the user (one number, not personal data; it can be rebuilt as the maximum `seq` of
the user's outbox rows) — and, if the snapshot database can roll back, keep it outside that
database, otherwise the mark rolls back too. Raise the mark no later than the commit of the
snapshot that issues the new `seq` (in the same transaction, or before it when the mark is kept
elsewhere: a mark that is too high is harmless, one that lags reissues `seq` values), and rebuild
it from the outbox only if executed outbox rows are kept. Start the user with `start` and this
mark (with the event of the update, section 1, step 3), or with `restart` and a placeholder
snapshot whose `at` names no message. `restart` takes no event: a user who comes back with an
update (a button of an old message) loses it there and lands on the initial message; `step`
with the placeholder and the update's event processes it as `start` with the event does (it is
a recovery, section 6, and the reply carries `"recovered": true`):

```sh doctest
$ printf '%s\n' '{"id":1,"op":"start","seq":41}' '{"id":2,"op":"restart","snapshot":{"at":"-","seq":41}}' \
>   '{"id":3,"op":"start","seq":41,"event":"btn:order"}' \
>   '{"id":4,"op":"step","snapshot":{"at":"-","seq":41},"event":"btn:order"}' \
>   | figram-engine examples/pizza.bot.json
{"id":1,"snapshot":{"at":"menu","seq":42},"actions":[{"send":"menu"}]}
{"id":2,"snapshot":{"at":"menu","seq":42},"actions":[{"send":"menu"}]}
{"id":3,"snapshot":{"at":"catalog","resume":{"order":"catalog"},"seq":42},"actions":[{"send":"catalog"}]}
{"id":4,"snapshot":{"at":"catalog","resume":{"order":"catalog"},"seq":42},"actions":[{"send":"catalog"}],"recovered":true}
```

Here 41 is the high-water mark: the engine starts the user at the initial message, and the
first transition gets `seq` 42; with the update's event `btn:order` (an arrow of the initial
message) the user goes straight to the catalog. Neither `start` nor the placeholder knows the old timers: in the
same operation remove from the scheduler all of the user's events with a token not greater than
the mark (new timers get greater tokens, so the order of these operations does not matter).

## 5. Engine failures

The Python reference client [`clients/python/figram_client.py`](../clients/python/figram_client.py)
distinguishes a failure of one call from a failure of the process.

* A **request timeout** (`timeout`, 10 s by default) fails only that call: the late reply is
  discarded, and the shared process keeps serving other users. The process is replaced only if
  it hangs — with unanswered requests it writes nothing for `hang_timeout` seconds (by default
  max(60, 3 × timeout)) — or breaks the protocol: invalid JSON or UTF-8, a reply line longer
  than `max_line` (16 MiB by default), an unknown or missing `id`, a reply out of request
  order, a reply of the wrong shape, end of output, a crash. Then all of its requests fail, and
  the next call starts a new process.
* An **error reply with `"id": null`** (the engine could not parse the request line) belongs,
  by reply order ([format.md](format.md), section 8), to the oldest unanswered request and fails
  only that request; the process is not replaced.
* **Restart with backoff.** If the processes of a slot keep crashing without answering a single
  request (the document does not load, the binary is broken), the first replacement starts at
  once and each further one is delayed twice as long as the previous: 0.1 s, 0.2 s, … up to
  30 s (`restart_backoff`). During the pause calls fail at once
  with the reason of the last failure — the exit status and the tail of the engine's stderr. A
  process that answered at least one request is replaced immediately after a failure.

Every client failure is an `EngineError` (an exception raised by the host's own `guards`
function propagates unchanged; a misuse of the client API, such as `processes` below 1 or
`guards` passed to `start` without `event`, raises `ValueError` at the call). `retryable = True` means a process failure or a
timeout: the host repeats such requests under the rules of section 1 (an engine step is a pure
function; repeating it is safe while the transaction is not committed), with a pause between
attempts. Limit retries after timeouts (for example to three): the process is single-threaded,
and a request that it cannot finish in time occupies it completely when retried forever —
neighbours on the process get timeouts although the process is healthy and is not replaced;
after the limit the event goes to the failure queue. `retryable = False` means an engine reply
`{"error": ..., "code": ...}` or an argument that cannot be sent. Such a failure is
deterministic: a retry gives the same error, so the event goes to the failure queue with an
alert instead of being retried forever. Distinguish errors by `code` (`EngineError.code`), not
by text ([format.md](format.md), section 8): `bad_snapshot` — the stored snapshot is corrupted
or was written by a newer engine (repair — section 4); `seq_overflow` — the counter is
exhausted; `parse_error`, `too_large`, `bad_request` — a bug in the host's request. The client
also accepts an error reply without `code` (`EngineError.code` is then `None`); the engine
always sends `code`, and the protocol validators (`clients/python/protocol.py`, `checkReply` in
`clients/go`) reject an error reply without it.

The document is loaded before the process answers its first request, so large documents need a
separate start timeout. When it starts a process, the client sends `hello` and waits for the
reply within `start_timeout` (600 s by default, `None` — no limit): the `Engine` constructor
returns when all processes are loaded or raises `EngineError` with the reason; calls made while
a replacement process loads wait for it, and their `timeout` counts from readiness. A host with
its own client should do the same: wait for the `hello` reply at least as long as the document
takes to load, otherwise the process is replaced before it finishes loading. Load times for
large documents are given in [testing.md](testing.md), "Performance".

`Engine.step` as a whole — the condition request, the condition function and the repeat with
the values — fits into `step_timeout` (2 × timeout by default). `close()` closes the processes
concurrently, with one shared `timeout`.

## 6. Updating the bot document

* Load the new document version into the engine as a whole; user snapshots are not migrated.
* If the snapshot's message no longer exists, the engine starts the user over and replies
  `"recovered": true` — worth logging. The old timers are cancelled and the event of that
  request is processed as at the bot's initial message, as for a new user (section 1, step 3):
  an arrow of the initial message or of a section around it leads straight to its target,
  otherwise the user lands on the initial message. The step can therefore answer `need`.
  Do not deliver the event again after `"recovered": true`: version 0.1.0 dropped the event, and
  this document then suggested delivering it again from the new snapshot; with 0.2.0 that
  processes it twice under a new `seq` — an arrow of the root section, such as `/start` with
  `save_ref`, makes two transitions, and with the keys `user:seq:index` of section 1 its actions
  run twice.
* If a remembered place no longer exists, "resume" leads to the section's initial message. If
  the place to go back to (`back`) no longer exists, "back" leads to the arrow's `to`.
  Recovery keeps `back`, so after it `back` can name the message the user landed on: "back"
  there sends the same message again until the user leaves it another way.
* The engine rejects a snapshot with an unknown field or version: such a snapshot was written
  by a newer engine, so the engine must not be rolled back without rolling back the snapshots.
* A message that became a section also counts as gone: the user starts over from the bot's
  initial message (`"recovered": true`), not from the section's. A remembered place moved to another
  section counts as gone.
* Snapshot timers are not migrated. If the user's current message has a timer that was renamed,
  removed or moved to another message in the new version, the firing `timer:<old name>` is
  ignored: the engine accepts a timer only if the current message owns it in the loaded version
  (and the token matches), even if an enclosing section has an arrow for that event. When the
  user leaves the message, the engine cancels its timers by the new version and every other
  timer of the snapshot (`cancel_timer` with the old name), so the old timer's entry does not
  survive the first move to another message (a variant without a target keeps it). A new timer of the current message starts only on the next
  entry into it; if the reminder matters, `restart` the affected users (section 4). Removing
  the old timers' events from the scheduler in advance is not required — like any cancellation
  under the rule of section 3, it is an optimisation.
* Waits for outcomes are kept: a wait of an action that is removed or loses its outcomes in the
  new version still accepts its outcome, and the outcome then only removes the wait (the new
  version can have no arrow for it, V5 of format.md, section 3); a wait of an action that is
  still declared with that outcome is handled by the arrows of the new version.
* Renamed conditions and actions: a host built for the old version passes a condition under its
  old name — the engine ignores it as extra and answers `need` again. Deploy the host and the
  document together; `hello` does not report the document version.

## 7. What is tested

`tests/engine/host_sim.py` models production: 30 users go through a pizza order, every event
is delivered twice and concurrently, 16 threads, a separate outbox executor, a separate executor
of background actions, an inbound queue of outcome events, and failures are injected at random
in five places (before commit, after commit, between executing an action and marking it
executed, between the answer of the CRM and the transaction that records it, between processing
an outcome and removing it from the inbound queue). Each user sends the next event for the
message of the committed snapshot and waits while the snapshot waits for an outcome. Conditions
are computed from data: the cart from executed `add_to_cart`/`clear_cart`, the delivery zone
from the address text, `address_rejected` from the details of the outcome; every third user
first presses "Checkout" with an empty cart, half of the users enter an address outside the
zone. The CRM answers `create_order` with `created`, or `rejected` (an address it does not
deliver to — the user types another one — or another reason — the user retries), or does not
answer at all, so the host delivers `failed` after three attempts (section 2, "Outcomes"), or
creates the order and loses the answer three times, and the host reconciles by the key of the
call and delivers `created`; the
outcome event is recorded in the transaction that marks the action executed and goes through
section 1 with the `update_id` `call:create_order:<token>`. The kitchen is busy on the first
attempt of every `notify_kitchen` (a background action). The test requires that
every user ends where their data leads (`done` with exactly one order, or `pickup` without
one), that no update is lost (the final snapshot equals a sequential replay of the processed
events with the same conditions), that no action stays unexecuted, that every call with a
token got exactly one outcome event, processed once, and that no wait is left in a snapshot. A
second scenario makes the kitchen fail for good for half of 12 users: their `notify_kitchen`
goes to the failure queue with an alert and every user still goes back to the menu and opens
the catalog. A stale timer event after the timer was restarted is ignored. A third scenario loses the
snapshot row of 12 users (after an order, or at the cart while the reminder runs): the old
timer event that arrives without a snapshot is dropped, and the user comes back with a button
of an old message, started with `start`, the update's event and the high-water mark (section 1,
step 3; section 4), and orders again with every action executed. `./verify.sh` runs it.

Controls show that the test detects violations: a host without duplicate filtering and
without idempotency keys (run by the test itself) produces duplicate orders; a host that
marks `create_order` executed and records its outcome afterwards, in a separate step (run by
the test itself), loses outcomes, and its users wait on `placing` forever; a host that
delivers `failed` without reconciling a call whose answer was lost (run by the test itself)
produces a second order on the retry; a host that runs
background actions in the user's queue, as sequential actions (run by the test itself), blocks
the users whose kitchen is down; a host that starts a
user without a snapshot without the update's event, or without the high-water mark (both run by
the test itself), loses the button or the actions of the second order; and a host without
the barrier (`host_sim.py ENGINE DOC --no-barrier`, conditions read before the previous step's
actions are executed; not part of `./verify.sh`) takes wrong branches.

`host_sim.py` does not model database rollbacks (and the run ids of timers and outcomes), user
resets, document updates (section 6), shared scarce resources, forged timer events, an executor
that breaks the order, outcome events for a user without a snapshot, background actions with
outcomes, or users who act while a wait is pending (outcomes that arrive on another message),
so the rules about them (section 1, steps 3 and 4; sections 2, 3, 4 and 6) are not tested by
`./verify.sh`; the engine's side of them is tested by `tests/engine/protocol_edges.py`,
the reference scenarios and `tests/compiler`. The engine side of the reset
procedure (section 4) is checked by the example above (`ci/check_docs.py` runs it) and by
`tests/engine/protocol_edges.py` (`restart` and `start` with `seq`).

The client is tested by `tests/engine/client_faults.py` — 13 kinds of engine failure on a
request (8 threads each), 11 before readiness (before a valid `hello` reply), and 300 concurrent
requests to a process that does not read its input: every request fails without hanging and
the process is replaced — and by `tests/engine/client_robustness.py` (checks R1–R20: waking
waiters, resources after hundreds of replacements, `close()`, the start phase, a timeout that
does not kill the shared process, `id: null`, the restart backoff, the reply-line and step-time
limits, strict UTF-8 and lone surrogates, a line the engine cannot parse).

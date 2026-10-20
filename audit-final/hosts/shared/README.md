# Shared artefacts for the production-like hosts (Go and Python)

Owner: host-python agent (branch `audit/host-python`, generator `audit/host-python/gen_corpus.py`).
Frozen: see `VERSION` (the SHA-256 of every file is listed there; consumers should verify).

```
bots/       pizza, pizza.v2, booking, support, support.v2, reminders, reminders.v2  (*.bot.json)
domain.md   guards and calls: how each is computed from / applied to the domain DB (normative)
corpus/     <scenario>-<seed>.jsonl deterministic workloads
VERSION     freeze marker + checksums
```

Every bot passes `figram check` (logs: `/home/user/figram-audit/agents/host-python/LOGS/figram-check-*.log`,
binary `bin/figram` = CI-verified official fresh build).

## Bots

| doc | what it exercises |
|---|---|
| `pizza` | byte copy of `examples/pizza.bot.json` (order, guards `cart_not_empty`, `in_delivery_zone`, timer `cart_reminder`, resume) |
| `pizza.v2` | **deleted** message `pick_toppings` (current message / history target of some users), timer `cart_reminder` **renamed** `cart_nudge` (1 h) |
| `booking` | scarce resource: `btn:slot` → `confirm` → guard `slot_free` → `reserve_slot` + external `notify_calendar`; hold timer `hold` (10 min) with a timer arrow; `has_booking` |
| `support` | nested sections `support/billing`, `support/tech`; escalation arrow on the parent section `support` (guard `can_escalate`), guarded timer arrow `timer:sla` without an else-variant; `cmd:help` → `faq` → `btn:back` with `resume: true` back into the conversation |
| `support.v2` | `b_wait` **deleted** (replaced by `b_pending`), timer `sla` **renamed** `sla2` (30 min) |
| `reminders` | timers start/cancel/re-arm (`btn:snooze` self-loop), timer arrows, guarded timer arrow with else (`remind`), guarded timer arrow without else (`nag`, see ET-06) |
| `reminders.v2` | timer `remind` **renamed** `ping` (90 s) |

## Corpus line format (`corpus/<scenario>-<seed>.jsonl`)

One JSON object per line, processed strictly in file order. First line is always
`{"ctl":"init","bot":"<family>","scenario":..,"seed":..[,"engine_proxy":true]}` — every user of the
file starts on that bot family (`pizza`, `booking`, `support`, `reminders`); `engine_proxy` means the
file contains `engine_fault` lines.

### Updates (Telegram-like)
`{"user":"u1","update_id":1017,"event":"btn:add"[,"arg":"L"][,"dup":2][,"delay_ms":20]}`
* `update_id` is globally increasing inside a file (like Telegram). Dedup key = (user, update_id).
* `dup: k` — deliver k copies **concurrently** (they must be deduplicated).
* `delay_ms` — the deliverer waits that long before sending the line.
* Delivery semantics of the reference runner: an update of user U is sent only after every earlier
  update of U was acknowledged (Telegram per-chat ordering); updates of different users are pipelined
  (window 32). Unacknowledged/NACKed updates are redelivered until acknowledged; after a host crash all
  unacknowledged updates are redelivered to the restarted host.
* The first update of an unknown user creates them with `start`; its event is consumed by that.

### Adversarial raw updates (scenario `adversarial` only)
`{"raw": <any JSON value>}` — delivered as the update body as-is; `{"raw_text":"<text>"}` — the body is
this raw text (invalid JSON, duplicate keys...). Expected: rejected (acknowledged, no state change at
all). `{"raw": {...}, "expect": "no_effect"}` — a well-formed update with unknown extra fields: may be
accepted, must not change any state (event is a no-op).

### Control lines (`ctl`) — every control line is a barrier: all in-flight updates acknowledged and the
outbox drained (unless the executor is paused) before it executes.
| line | meaning |
|---|---|
| `{"ctl":"crash","at":P[,"on":F]}` | arm a crash point; the first time the host passes P (matching filter F) the host process dies by a real `kill -9` (Python: `os.kill(getpid(), SIGKILL)`), then it is restarted from the same DB. P ∈ `before_commit` (step computed, transaction not committed), `after_commit` (committed, not acknowledged), `before_effect` (outbox executor picked a call/send, nothing done), `after_effect_before_ack` (effect done at the external service / local txn not committed, outbox row not marked). F: an event name (`payment:success`, `timer:remind`) for step points; `call:<name>` / `send` for executor points. An armed point that has not fired is re-armed after every restart. |
| `{"ctl":"restart_host"}` | graceful stop + start on the same DB |
| `{"ctl":"upgrade","bot":"pizza.v2"}` | switch all users of family `pizza` (and new users) to doc `pizza.v2`; snapshots are not migrated |
| `{"ctl":"advance_ms","ms":N}` | advance the simulated clock by N ms; the host's scheduler fires every due timer (ordered by due, user, name, token) through the normal step path; each fire's event time is its due time |
| `{"ctl":"timer_mode","ignore_cancel":true}` | from now on canceled/superseded timers still fire at their due time (models callbacks already in flight that cannot be canceled) → stale tokens reach the engine |
| `{"ctl":"timer_mode","dup":2}` | every timer fire is delivered twice concurrently (double callback) |
| `{"ctl":"replay_stale","user":U}` | re-deliver every canceled/orphaned/fired timer of U with its original update id `timer:<name>:<token>` |
| `{"ctl":"reset_user","user":U}` | "delete my data, keep the account" (domain.md §5) |
| `{"ctl":"external","target":"crm|tg|all","mode":M,"count":n[,"ms":x]}` | fault mode of the simulated external services for the next n requests: `500`, `429` (retry after ms), `timeout` (effect applied, response lost), `timeout_before` (not applied), `dup` (network delivers the request twice), `delay` (respond after ms; host client timeout 1 s), `reorder` (concurrent requests applied in shuffled order), `ok` (clear) |
| `{"ctl":"executor","mode":"pause|resume"}` | pause the background outbox executor (rule G1 drains still run inside steps) |
| `{"ctl":"db_lock","ms":N}` | an outside process holds an EXCLUSIVE lock on the host DB for N ms while traffic continues (DB unavailable) |
| `{"ctl":"engine_fault","mode":M}` | the next engine request fails: `kill` (engine process SIGKILLed → pipe break), `hang`, `corrupt`, `wrong_id`, `truncate`, `garbage`, `bad_snapshot`, `bad_action` (Python: a real proxy process `evil_proxy.py` between host and engine). FFI hosts: map what is applicable, mark the rest N/A |
| `{"ctl":"corrupt_snapshot","user":U,"mode":M}` | written directly into the stored snapshot of U while the host is quiescent: `garbage`, `not_object`, `unknown_field`, `bad_version`, `wrong_type`, `missing_at`, `huge_seq`, `rollback` (an older valid snapshot), `deleted_node`, `null` |
| `{"ctl":"barrier"}` | just the barrier |

## Scenarios

| file | bot | content |
|---|---|---|
| `order-{1,2}` | pizza | 18 users: happy, empty-cart checkout, out-of-zone (pickup), help/resume, cancel, double payment webhook, 2 h reminder + resume, reset + second order (ET-01 path); 18 crash points, CRM/TG 500/429/timeout/dup/delay/reorder, executor pause, restart |
| `booking-{1,2}` | booking | 18 users; 4 races for scarce slots (concurrent confirmations), hold-timer expiry, cancellation freeing capacity, unknown slot, crashes at all 4 points |
| `support-1` | support → support.v2 | SLA timer escalation, escalation arrow on parent section, faq/resume, upgrade mid-conversation (deleted current message, deleted history target, renamed timer) |
| `reminders-{1,2}` | reminders | re-arm, cancel, stale tokens (`ignore_cancel`), double callbacks, crash while a timer fires, restart with pending timers, reset with pending timers, replay of stale fires |
| `migration-1` | pizza → pizza.v2 | users at a deleted message, at help with a deleted history target, with a pending renamed timer, new users on v2 |
| `failures-{1,2}` | pizza | 10 crash points, 2 × DB lock 2.5 s, engine kill ×2 / hang / corrupt / truncate, external fault storm, restart |
| `load_u{1,10,100,1000}-7` | pizza | N users × 8 updates, no faults |
| `adversarial-1` | pizza | 30 malformed/forged raw updates, 10 corrupted snapshots, 8 engine protocol faults, mixed into normal traffic |

## Output (`/home/user/figram-audit/hosts/out/<go|python>/<scenario>-<seed>.json`)
As in HOSTS_SPEC.md. Python additions: `transitions[].kind` (`start|step|repair|reset`), `arg`, `token`,
`t_ms` (simulated clock), `recovered`; `invariant_failures` (details), `latency`, `faults_injected`.
`effects`: CRM effects (`call` = CRM op) deduplicated by key + Telegram deliveries deduplicated by key.

### Documented differences between runs
* booking: which of several concurrent contenders wins a scarce slot is timing-dependent; compare
  aggregates (final `at` multiset, reservations per slot, number of transitions/effects).
* Telegram duplicate deliveries (at-least-once) are timing-dependent; compare deduplicated effects.
* `transitions[].t_ms` of updates equals the simulated clock at processing time (deterministic);
  wall-clock values are never part of the output.

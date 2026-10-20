# Production-like hosts — shared spec (Go and Python)

Two independent test applications, written from scratch (do NOT reuse `ffi/go-example` or
`host_sim.py` code; read them only as reference). Python host runs the engine as a child process
(`figram-engine`, CI-verified `bin/figram-engine` or a fresh build). Go host links the REAL
`libfigram.a` via cgo (from `/home/user/figram-audit/fresh/ffi-out/`); if native linking is not
possible the Go path is BLOCKED — never replace it with a mock or with the process engine.

## Shared artefacts (owner: Python-host agent creates them first; Go-host agent only reads)

`/home/user/figram-audit/hosts/shared/`
* `bots/*.bot.json` — at least: `pizza` (copy of examples/pizza.bot.json), `booking` (online
  booking with a scarce resource: pick slot → confirm with guard `slot_free` → `reserve_slot` call),
  `support` (nested sections, escalation arrow on a parent section, history/resume back into the
  conversation), `reminders` (timers: start/cancel/re-arm, timer arrow, guarded timer arrow), and v2
  variants for migration (`*.v2.bot.json`: deleted current message, deleted history target, renamed
  timer). Every doc must pass `figram check` (log it).
* `domain.md` — how each guard is computed from the domain DB and what each `call` does to it
  (e.g. `cart_not_empty` ⇔ cart rows > 0; `add_to_cart` inserts a row; `create_order` creates an order
  row keyed by the idempotency key; `reserve_slot` decrements capacity with a uniqueness constraint).
  Guards are computed from the domain DB at step time — never constant `true`.
* `corpus/<scenario>-<seed>.jsonl` — deterministic workloads. One JSON object per line, ordered:
  - `{"user":"u1","update_id":17,"event":"btn:add"}` — an incoming update (Telegram-like);
    optional `"dup":2` (deliver twice, concurrently) and `"delay_ms"`;
  - timer fires are produced by the host's own scheduler, not by the corpus;
  - control lines: `{"ctl":"crash","at":"before_commit|after_commit|before_effect|after_effect_before_ack"}`
    (next processed update crashes the host process at that point — use a real kill -9 of the host
    process or os._exit, then restart it from the same DB), `{"ctl":"upgrade","bot":"pizza.v2"}`,
    `{"ctl":"advance_ms","ms":7200000}` (simulated clock for timers), `{"ctl":"external","mode":"500|429|timeout|ok"}`
    (fault mode of the simulated CRM/Telegram adapter), `{"ctl":"restart_host"}`.
  Scenarios to cover (README of the audit task): 1 order, 2 booking with conflicts, 3 support +
  history + bot change mid-conversation, 4 reminders (stale token, double callback, callback after
  restart), 5 migration, 6 failure points incl. DB unavailable and engine/FFI pipe break,
  7 load 1/10/100/1000 users, 8 protocol-adversarial (malformed requests/snapshots stored in DB,
  wrong ids, corrupted responses) — ordinary robustness only, no resource-exhaustion inputs.

## Host requirements (both)

* Persistent SQLite DB: users(snapshot JSON, version/optimistic-lock column, doc version), processed
  update ids, outbox(key UNIQUE, user, action JSON, state), domain tables, scheduled timers,
  external-effects log of the simulator. Restart of the host process must not lose state.
* Per-user serialization (lock or optimistic version check); different users in parallel.
* Step: read snapshot → `step` → on `need` compute guards from domain DB → retry → ONE transaction:
  new snapshot + processed update id + outbox rows. Then ack.
* Outbox executor: separate worker; idempotency key per action. Choose and DOCUMENT the key scheme;
  note the known defect ET-01 (keys `user:seq:index` collide after `start`; see
  `/home/user/figram-audit/agents/engine-timers/REPORT.md`) — your host must not be vulnerable
  (e.g. persistent per-user high-water seq + `restart`, or keys with a per-user epoch).
* Guards that depend on effects of earlier calls of the same user: define the rule your host uses
  (e.g. drain that user's pending outbox calls before computing guards) and test it.
* External adapter = local simulator with modes 200/500/429/timeout, duplicate delivery, delay,
  reorder. Never call real Telegram/CRM/payment services.

## Output (for Go-vs-Python comparison)

`/home/user/figram-audit/hosts/out/<go|python>/<scenario>-<seed>.json`:
```
{"users": {"u1": {"snapshot": {...}, "transitions": [{"update_id":..,"event":..,"seq":..,"at":..,
   "actions":[...]}], "doc": "pizza"}},
 "effects": [{"key":..,"user":..,"call"|"send":..}],      # executed external effects, deduplicated
 "invariants": {"name": "PASS|FAIL", ...}, "counts": {...}}
```
Sort users by id; transitions in commit order. Differences allowed only where documented in the
report (e.g. timing-dependent timer fires).

## Invariants to check (at least)

one order per idempotency key; no second order after crash/duplicate; snapshot seq strictly +1 per
committed transition; no lost acknowledged update; stale timer token never produces an effect; no
pending outbox after drain; other users unaffected by one user's malformed data; host restart
reproduces the same final state.

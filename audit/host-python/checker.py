"""Invariant checker. Reads the host DB, the external simulator DB and the runner's ack log.
It does NOT reuse the host's guard code: guards are re-derived from an independent model that
replays the committed transitions (actions come from the engine) in commit order."""
from __future__ import annotations

import json
import os
import re
import sqlite3
from collections import Counter, defaultdict

ZONES = {"Lenina", "Mira", "Pushkina", "Gagarina"}
SLOTS = {"s1": 1, "s2": 2, "s3": 4, "s4": 10}
EXTERNAL = {"create_order": "create_order", "notify_calendar": "calendar", "create_ticket": "ticket_create",
            "log_message": "ticket_message", "escalate": "ticket_escalate", "close_ticket": "ticket_close",
            "notify_owner": "notify"}
DOMAIN_Q = [
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


def street_of(arg):
    if not isinstance(arg, str):
        return ""
    m = re.match(r"\s*([^\s,]+)", arg)
    return m.group(1) if m else ""


def q(c, sql, args=()):
    return [tuple(r) for r in c.execute(sql, args).fetchall()]


def open_dbs(work):
    h = sqlite3.connect(f"file:{os.path.join(work, 'host.sqlite')}?mode=ro", uri=True)
    e = sqlite3.connect(f"file:{os.path.join(work, 'ext.sqlite')}?mode=ro", uri=True)
    return h, e


def dump_state(work):
    h, e = open_dbs(work)
    d = {
        "users": q(h, "SELECT user,doc,snapshot,hw_seq FROM users ORDER BY user"),
        "outbox": q(h, "SELECT key,state FROM outbox ORDER BY key"),
        "timers": q(h, "SELECT user,name,token,due_ms,state,delivered FROM timers ORDER BY id"),
        "processed": q(h, "SELECT COUNT(*) FROM processed"),
        "transitions": q(h, "SELECT COUNT(*) FROM transitions"),
        "domain": {k: q(h, s) for k, s in DOMAIN_Q},
        "ext_requests": q(e, "SELECT COUNT(*) FROM requests"),
        "tg": q(e, "SELECT COUNT(*) FROM tg_deliveries"),
        "crm": q(e, "SELECT COUNT(*) FROM crm_effects"),
    }
    h.close()
    e.close()
    return d


def transitions(h):
    rows = q(h, "SELECT n,user,update_id,event,arg,token,kind,seq_before,seq,at,doc,actions,recovered,t_ms,guards,"
                "changed,note FROM transitions ORDER BY n")
    out = []
    for r in rows:
        out.append({"n": r[0], "user": r[1], "update_id": r[2], "event": r[3], "arg": r[4], "token": r[5],
                    "kind": r[6], "seq_before": r[7], "seq": r[8], "at": r[9], "doc": r[10],
                    "actions": json.loads(r[11] or "[]"), "recovered": bool(r[12]), "t_ms": r[13],
                    "guards": json.loads(r[14]) if r[14] else None, "changed": bool(r[15]), "note": r[16]})
    return out


def user_views(work):
    h, e = open_dbs(work)
    users = {}
    for u, doc, snap, hw in q(h, "SELECT user,doc,snapshot,hw_seq FROM users ORDER BY user"):
        try:
            s = json.loads(snap) if snap else None
        except ValueError:
            s = {"_unparseable": snap}
        users[u] = {"snapshot": s, "doc": doc, "transitions": []}
    for t in transitions(h):
        if t["changed"] and t["user"] in users:
            users[t["user"]]["transitions"].append(
                {k: t[k] for k in ("update_id", "event", "arg", "token", "kind", "seq", "at", "t_ms", "actions",
                                   "recovered")})
    effects = []
    for key, user, op in q(e, "SELECT key,user,op FROM crm_effects ORDER BY key"):
        effects.append({"key": key, "user": user, "call": op})
    for key, user, text in q(e, "SELECT key,user,text FROM tg_deliveries GROUP BY key ORDER BY key"):
        effects.append({"key": key, "user": user, "send": text})
    effects.sort(key=lambda x: (x["key"], "call" in x))
    domain = {k: q(h, s) for k, s in DOMAIN_Q}
    h.close()
    e.close()
    return users, effects, domain


def comparable(view):
    return [(t["event"], t["arg"], t["token"], t["kind"], t["seq"], t["at"], json.dumps(t["actions"]), t["recovered"])
            for t in view["transitions"]]


class Model:
    """Independent reference of the domain (shared/domain.md), driven by committed actions."""

    def __init__(self):
        self.cart = Counter()
        self.addr = {}
        self.sel = {}
        self.res = defaultdict(set)
        self.ticket = {}
        self.msgs = Counter()
        self.rem = {}
        self.violations = []

    def guard(self, name, user, arg):
        if name == "cart_not_empty":
            return self.cart[user] > 0
        if name == "in_delivery_zone":
            return street_of(arg) in ZONES
        if name == "slot_free":
            s = self.sel.get(user)
            return s in SLOTS and user not in self.res[s] and len(self.res[s]) < SLOTS[s]
        if name == "has_booking":
            return any(user in us for us in self.res.values())
        if name == "can_escalate":
            return self.ticket.get(user) == "open"
        if name == "ticket_open":
            return self.ticket.get(user) in ("open", "escalated")
        if name == "has_reminder":
            return self.rem.get(user) not in (None, "")
        if name == "nag_enabled":
            return (self.rem.get(user) or "").startswith("!")
        raise KeyError(name)

    def apply(self, t, call):
        u, arg = t["user"], t["arg"] if isinstance(t["arg"], str) else ""
        where = f"{u} seq {t['seq']} {call}"
        if call == "clear_cart":
            self.cart[u] = 0
        elif call == "add_to_cart":
            self.cart[u] += 1
        elif call == "save_address":
            self.addr[u] = arg
        elif call == "create_order":
            if self.cart[u] <= 0:
                self.violations.append(f"order with empty cart: {where}")
            if street_of(self.addr.get(u)) not in ZONES:
                self.violations.append(f"order outside delivery zone ({self.addr.get(u)!r}): {where}")
            self.cart[u] = 0
        elif call == "choose_slot":
            self.sel[u] = arg
        elif call == "reserve_slot":
            s = self.sel.get(u)
            if s not in SLOTS or u in self.res[s] or len(self.res[s]) >= SLOTS[s]:
                self.violations.append(f"reservation beyond capacity/duplicate on {s}: {where}")
            else:
                self.res[s].add(u)
        elif call == "release_hold":
            self.sel.pop(u, None)
        elif call == "cancel_booking":
            for us in self.res.values():
                us.discard(u)
        elif call == "create_ticket":
            self.ticket[u] = "open"
            self.msgs[u] = 1
        elif call == "log_message":
            if u not in self.ticket:
                self.violations.append(f"message without ticket: {where}")
            self.msgs[u] += 1
        elif call == "escalate":
            if self.ticket.get(u) not in ("open", "escalated"):
                self.violations.append(f"escalation without open ticket: {where}")
            self.ticket[u] = "escalated"
        elif call == "close_ticket":
            self.ticket[u] = "closed"
        elif call == "save_reminder":
            self.rem[u] = arg or None
        elif call == "clear_reminder":
            self.rem.pop(u, None)
        elif call == "log_ping":
            if self.rem.get(u) in (None, ""):
                self.violations.append(f"ping without reminder: {where}")

    def reset(self, family, u):
        if family == "pizza":
            self.cart[u] = 0
            self.addr.pop(u, None)
        elif family == "booking":
            self.sel.pop(u, None)
        elif family == "reminders":
            self.rem.pop(u, None)


def inv(ok, detail=None):
    return ("PASS" if ok else "FAIL"), detail


def check(work, items, runner, main_res, refs, family):
    h, e = open_dbs(work)
    tr = transitions(h)
    changed = [t for t in tr if t["changed"]]
    res = {}
    details = {}

    def put(name, ok, detail=None):
        res[name], d = inv(ok, detail)
        if not ok:
            details[name] = detail

    # intents = call/send actions of committed transitions, key = user:seq:index
    intents = []
    for t in changed:
        for i, a in enumerate(t["actions"]):
            if "call" in a or "send" in a:
                intents.append((f"{t['user']}:{t['seq']}:{i}", t["user"], a.get("call"), a.get("send")))
    key_count = Counter(k for k, *_ in intents)
    reused = [k for k, n in key_count.items() if n > 1]
    conflicts = q(e, "SELECT key, op FROM key_conflicts")
    put("idempotency_keys_unique_lifetime", not reused and not conflicts,
        {"reused_keys": reused[:10], "crm_key_conflicts": conflicts[:10]})

    # external calls: exactly one applied CRM effect per intent, matched by key
    ext_intents = [(k, u, c) for k, u, c, s in intents if c in EXTERNAL]
    eff = {k: (u, op) for k, u, op in q(e, "SELECT key,user,op FROM crm_effects")}
    missing = [k for k, u, c in ext_intents if k not in eff]
    per_user_int = Counter((u, EXTERNAL[c]) for k, u, c in ext_intents)
    per_user_eff = Counter(eff.values())
    put("external_effect_once_per_intent", not missing and per_user_int == per_user_eff,
        {"intents_without_effect": missing[:10],
         "diff": {f"{k[0]}/{k[1]}": [per_user_int.get(k, 0), per_user_eff.get(k, 0)]
                  for k in set(per_user_int) | set(per_user_eff) if per_user_int.get(k, 0) != per_user_eff.get(k, 0)}})
    if any(c == "create_order" for _, _, c in ext_intents) or family == "pizza":
        orders = Counter(u for (u,) in q(e, "SELECT user FROM crm_orders"))
        want = Counter(u for k, u, c in ext_intents if c == "create_order")
        put("one_order_per_payment", orders == want,
            {u: [want.get(u, 0), orders.get(u, 0)] for u in set(orders) | set(want) if orders.get(u, 0) != want.get(u, 0)})

    # outbox: every intent present and done; nothing pending/failed
    ob = {k: s for k, s in q(h, "SELECT key,state FROM outbox")}
    not_done = [k for k, *_ in intents if ob.get(k) != "done"]
    put("actions_complete", not not_done, {"not_done": not_done[:10], "n": len(not_done)})
    pend = q(h, "SELECT key,state,last_error FROM outbox WHERE state IN ('pending','failed')")
    put("outbox_drained", not pend, pend[:10])

    # seq strictly +1 per committed transition, over the whole lifetime (incl. reset/repair)
    bad_seq = []
    last = {}
    for t in changed:
        p = last.get(t["user"], 0)
        if t["seq"] != p + 1:
            bad_seq.append((t["user"], t["kind"], t["event"], p, t["seq"]))
        last[t["user"]] = t["seq"]
    put("seq_plus_one", not bad_seq, bad_seq[:10])

    # liveness: every delivered update was eventually acknowledged (none abandoned after max NACKs)
    put("all_updates_acked", not runner.abandoned, runner.abandoned[:10])

    # no lost acknowledged update
    proc = {(u, uid): r for u, uid, r in q(h, "SELECT user,update_id,result FROM processed")}
    lost = [a for a in runner.acks if not a["raw"] and a["status"] != "rejected" and (a["user"], f"tg:{a['uid']}") not in proc]
    put("no_lost_ack", not lost, lost[:10])

    # timers: a timer-caused transition must match the active (token, due) instance
    active = defaultdict(dict)
    stale, early = [], []
    for t in changed:
        u = t["user"]
        if t["event"] and t["event"].startswith("timer:") and t["kind"] == "step":
            name = t["event"][6:]
            cur = active[u].get(name)
            if cur is None or cur[0] != t["token"]:
                stale.append((u, name, t["token"], cur))
            elif t["t_ms"] < cur[1]:
                early.append((u, name, t["token"], t["t_ms"], cur[1]))
        if t["kind"] in ("repair", "reset"):
            active[u] = {}
        for a in t["actions"]:
            if "cancel_timer" in a:
                active[u].pop(a["cancel_timer"], None)
            elif "start_timer" in a:
                active[u][a["start_timer"]] = (a["token"], t["t_ms"] + a["ms"])
    put("stale_timer_no_effect", not stale, stale[:10])
    put("timer_not_early", not early, early[:10])

    # guards: recorded values == independent model; domain outcome checks
    m = Model()
    wrong = []
    for t in tr:
        if t["guards"]:
            for g, v in t["guards"].items():
                want = m.guard(g, t["user"], t["arg"])
                if want != v:
                    wrong.append((t["user"], t["seq"], t["event"], g, v, want))
        if t["kind"] == "reset":
            m.reset(family, t["user"])
        for a in t["actions"]:
            if "call" in a:
                m.apply(t, a["call"])
    put("guards_match_domain_model", not wrong, wrong[:10])
    put("domain_model_consistent", not m.violations, m.violations[:10])
    if family == "booking":
        over = q(h, "SELECT r.slot, COUNT(*), s.capacity FROM reservations r JOIN slots s ON s.slot=r.slot "
                    "WHERE r.active=1 GROUP BY r.slot HAVING COUNT(*)>s.capacity")
        put("no_overbooking", not over, over)
    if family == "support":
        crm_msgs = dict(q(e, "SELECT user, messages FROM crm_tickets WHERE ticket_id IN "
                             "(SELECT MAX(ticket_id) FROM crm_tickets GROUP BY user)"))
        diff = {u: [m.msgs[u], crm_msgs.get(u)] for u in set(crm_msgs) | set(k for k in m.msgs if m.msgs[k])
                if m.msgs[u] != crm_msgs.get(u)}
        put("ticket_messages_match", not diff, diff)

    # Telegram sends: at-least-once (NOT exactly-once); duplicates must be explained by a fault
    tg = Counter(k for (k,) in q(e, "SELECT key FROM tg_deliveries"))
    undelivered = [k for k, u, c, s in intents if s is not None and tg.get(k, 0) == 0]
    put("send_at_least_once", not undelivered, undelivered[:10])
    dups = {k: n for k, n in tg.items() if n > 1}
    flog = runner.fault_log
    slow = q(h, "SELECT COUNT(*) FROM outbox WHERE kind='send' AND last_error LIKE '%client_timeout%'")[0][0]
    explainers = slow > 0 or any((f["fault"] == "external" and f.get("target", "all") in ("tg", "all") and
                      f["mode"] in ("timeout", "dup", "delay")) or f["fault"] == "db_lock" or
                     f["fault"] == "crash"   # a kill -9 can land between Telegram's reply and marking the row
                     for f in flog)
    put("send_duplicates_explained", not dups or explainers,
        {"duplicated_keys": len(dups), "client_timeouts_on_send": slow})

    # faults really happened (no vacuous PASS)
    kills = [x for x in runner.exits if not x["expected"]]
    put("crashes_effective", len(kills) == runner.crash_armed == len(runner.crash_fired)
        and all(x["rc"] == -9 for x in kills) and not runner.armed,
        {"armed": runner.crash_armed, "fired": runner.crash_fired, "never_fired": runner.armed, "exits": kills})
    unconsumed = q(e, "SELECT target,mode,remaining FROM modes WHERE remaining>0")
    modes_seen = Counter(m_ for (m_,) in q(e, "SELECT mode FROM requests WHERE mode<>'ok'"))
    reorders = q(e, "SELECT COUNT(*) FROM reorders")[0][0]
    want_reorder = any(f["fault"] == "external" and f["mode"] == "reorder" for f in flog)
    put("external_faults_effective", not unconsumed and (reorders > 0 or not want_reorder),
        {"unconsumed": unconsumed, "seen": dict(modes_seen), "reordered_batches": reorders})
    if any(f["fault"] == "db_lock" for f in flog):
        put("db_lock_effective", runner.nacks > 0, {"nacks": runner.nacks})
    n_ef = sum(1 for f in flog if f["fault"] == "engine_fault")
    if n_ef:
        retries = q(h, "SELECT COUNT(*) FROM host_log WHERE kind='engine_retry'")[0][0]
        put("engine_faults_effective", retries >= n_ef, {"armed": n_ef, "engine_retries_logged": retries})

    # adversarial raw updates must be rejected without any state change
    raw_acks = [a for a in runner.acks if a["raw"]]
    no_effect_lines = {i for i, it in enumerate(items) if it.get("expect") == "no_effect"}
    accepted = [a for a in raw_acks if a["status"] != "rejected"
                and not (a["line"] in no_effect_lines and a["status"] in ("ignored", "dup"))]
    if any("raw" in it or "raw_text" in it for it in items):
        put("malformed_updates_rejected", not accepted and len(raw_acks) == sum(1 for it in items if "raw" in it or "raw_text" in it),
            {"accepted": accepted[:10], "raw_acks": len(raw_acks)})

    # restart of the host process reproduces the same state and executes nothing new
    b, a = main_res["restart_before"], main_res["restart_after"]
    diffk = [k for k in b if b[k] != a[k]]
    put("restart_reproduces_state", not diffk, diffk)

    # equality with the fault-free reference (correct host)
    users, effects, dom = user_views(work)
    if refs.get("clean"):
        ru, reff, rdom = user_views(refs["clean"])
        if family == "booking":
            # cross-user race on a scarce slot: WHICH user wins may legitimately differ; compare
            # order-independent aggregates (see README "documented differences")
            def agg(us, ef, dm):
                return {"final_at": sorted(Counter(v["snapshot"].get("at") for v in us.values()).items()),
                        "n_transitions": sum(len(v["transitions"]) for v in us.values()),
                        "reservations": sorted(Counter((r[2], r[3]) for r in dm["reservations"]).items()),
                        "effects": sorted(Counter(x.get("call") or "send" for x in ef).items())}
            A, B = agg(users, effects, dom), agg(ru, reff, rdom)
            put("equals_clean_run", A == B, {k: [A[k], B[k]] for k in A if A[k] != B[k]})
        else:
            du = [u for u in set(users) | set(ru) if u not in users or u not in ru or
                  comparable(users[u]) != comparable(ru[u]) or users[u]["snapshot"] != ru[u]["snapshot"]]
            ek = {(x["key"], x.get("call") or x.get("send")) for x in effects}
            rk = {(x["key"], x.get("call") or x.get("send")) for x in reff}
            dd = [k for k in dom if dom[k] != rdom[k]]
            first = None
            if du:
                u = sorted(du)[0]
                a_, b_ = comparable(users.get(u, {"transitions": []})), comparable(ru.get(u, {"transitions": []}))
                i = next((i for i in range(max(len(a_), len(b_))) if i >= len(a_) or i >= len(b_) or a_[i] != b_[i]), None)
                first = {"user": u, "index": i, "run": a_[i] if i is not None and i < len(a_) else None,
                         "ref": b_[i] if i is not None and i < len(b_) else None}
            put("equals_clean_run", not du and ek == rk and not dd,
                {"users": sorted(du)[:10], "first_diff": first, "effects_only_run": sorted(ek - rk)[:5],
                 "effects_only_ref": sorted(rk - ek)[:5], "domain_tables": dd})
    if refs.get("pristine") and refs.get("pristine") != refs.get("clean"):
        pu, peff, pdom = user_views(refs["pristine"])
        targets = {it["user"] for it in items if it.get("ctl") == "corrupt_snapshot"}
        du = [u for u in set(users) | set(pu) if u not in targets and
              (u not in users or u not in pu or comparable(users[u]) != comparable(pu[u])
               or users[u]["snapshot"] != pu[u]["snapshot"])]
        put("other_users_unaffected", not du, {"users": sorted(du)[:10], "targets": sorted(targets)})
        # targeted users: repaired (start with seq = high-water seq), seq continued, nothing reused
        rep = Counter(t["user"] for t in changed if t["kind"] == "repair" or t["recovered"])
        put("corrupted_users_repaired", all(rep[u] >= 1 or not users.get(u) for u in targets),
            {u: rep[u] for u in targets})
    counts = {
        "users": len(users), "transitions": len(changed), "steps_logged": len(tr),
        "updates_acked": sum(1 for a in runner.acks if not a["raw"]),
        "updates_abandoned": len(runner.abandoned),
        "acks_by_status": dict(Counter(a["status"] for a in runner.acks)), "nacks": runner.nacks,
        "crashes": len([x for x in runner.exits if not x["expected"]]), "host_restarts": runner.restarts,
        "recovered": sum(1 for t in changed if t["recovered"]), "repairs": sum(1 for t in changed if t["kind"] == "repair"),
        "resets": sum(1 for t in changed if t["kind"] == "reset"),
        "rejected": q(h, "SELECT COUNT(*) FROM rejected")[0][0],
        "outbox_rows": len(ob), "outbox_retried": q(h, "SELECT COUNT(*) FROM outbox WHERE attempts>1")[0][0],
        "crm_effects": len(eff), "crm_requests_by_outcome": dict(Counter(o for (o,) in q(e, "SELECT outcome FROM requests WHERE target='crm'"))),
        "tg_deliveries": sum(tg.values()), "tg_duplicate_keys": len(dups),
        "ext_fault_modes_seen": dict(modes_seen),
        "timers_by_state": dict(Counter(s for (s,) in q(h, "SELECT state FROM timers"))),
        "engine_retries": q(h, "SELECT COUNT(*) FROM host_log WHERE kind='engine_retry'")[0][0],
        "upgrades": q(h, "SELECT COUNT(*) FROM host_log WHERE kind='upgrade'")[0][0],
        "orphaned_timers": q(h, "SELECT COUNT(*) FROM timers WHERE state='orphaned'")[0][0],
        "idempotency_key_conflicts_at_crm": len(conflicts),
        "ext_reordered_batches": q(e, "SELECT COUNT(*) FROM reorders")[0][0],
    }
    h.close()
    e.close()
    return users, effects, res, details, counts


def pct(xs):
    if not xs:
        return None
    xs = sorted(xs)
    return {"n": len(xs), "p50": xs[int(0.5 * (len(xs) - 1))], "p95": xs[int(0.95 * (len(xs) - 1))],
            "p99": xs[int(0.99 * (len(xs) - 1))], "max": xs[-1]}


def build_output(work, items, name, runner, main_res, refs, opts):
    family = runner.family
    users, effects, res, details, counts = check(work, items, runner, main_res, refs, family)
    lat = runner.lat
    latency = {
        "host_update_ms": pct([x[0] / 1000 for x in lat]),
        "host_step_ms": pct([x[1] / 1000 for x in lat]),
        "e2e_ms": pct([x[2] / 1000 for x in lat]),
        "elapsed_s": round(main_res["elapsed_s"], 3),
        "throughput_updates_per_s": round(len(lat) / main_res["elapsed_s"], 1) if main_res["elapsed_s"] else None,
    }
    scen, _, seed = name.rpartition("-")
    return {"host": "python", "scenario": scen, "seed": int(seed) if seed.isdigit() else seed,
            "broken": opts.broken or None, "bot": family,
            "users": dict(sorted(users.items())), "effects": effects, "invariants": res,
            "invariant_failures": details, "counts": counts, "latency": latency,
            "faults_injected": runner.fault_log,
            "config": {"workers": opts.workers, "engines": opts.engines, "executors": opts.executors,
                       "window": opts.window, "sync": opts.sync, "engine": opts.engine}}

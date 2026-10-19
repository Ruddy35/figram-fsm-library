#!/usr/bin/env python3
"""Deterministic workload generator -> /home/user/figram-audit/hosts/shared/corpus/<scenario>-<seed>.jsonl
Format: see /home/user/figram-audit/hosts/shared/README.md. Same (scenario, seed) => byte-identical file."""
from __future__ import annotations

import json
import os
import random
import sys

OUT = sys.argv[1] if len(sys.argv) > 1 else "/home/user/figram-audit/hosts/shared/corpus"
ADDR_IN = ["Lenina 1", "Mira 15, kv 3", "Pushkina 10", "Gagarina 7"]
ADDR_OUT = ["Tverskaya 5", "Arbat 12", "lenina 1"]   # zones are case-sensitive
SIZES = ["S", "M", "L"]


class Gen:
    def __init__(self, scenario, seed, bot, **init):
        self.name = f"{scenario}-{seed}"
        self.rng = random.Random(f"{scenario}-{seed}")
        self.lines = [{"ctl": "init", "bot": bot, "scenario": scenario, "seed": seed, **init}]
        self.uid = 1000

    def upd(self, user, event, arg=None, dup=None, delay_ms=None):
        self.uid += 1
        d = {"user": user, "update_id": self.uid, "event": event}
        if arg is not None:
            d["arg"] = arg
        if dup:
            d["dup"] = dup
        if delay_ms:
            d["delay_ms"] = delay_ms
        self.lines.append(d)

    def ctl(self, **kw):
        self.lines.append(kw)

    def raw(self, body):
        self.lines.append({"raw": body})

    def interleave(self, scripts, dup_p=0.0, faults=(), delay_p=0.0):
        rem = {u: list(s) for u, s in scripts.items()}
        total = sum(len(s) for s in rem.values())
        faults = list(faults)
        pos = sorted(self.rng.sample(range(max(total, len(faults))), len(faults))) if faults else []
        k = fi = 0
        while any(rem.values()):
            while fi < len(faults) and pos[fi] <= k:
                self.ctl(**faults[fi])
                fi += 1
            u = self.rng.choice(sorted(x for x in rem if rem[x]))
            item = rem[u].pop(0)
            if item[0] == "@":
                self.ctl(**item[1])
            else:
                ev, arg = item
                self.upd(u, ev, arg, dup=2 if self.rng.random() < dup_p else None,
                         delay_ms=self.rng.choice([5, 20]) if self.rng.random() < delay_p else None)
            k += 1
        for f in faults[fi:]:
            self.ctl(**f)

    def fix_crash_filters(self):
        """An event-filtered crash point must be followed by a matching update; otherwise move it
        right before the last matching update (a crash point that never fires proves nothing)."""
        trig = {"call:create_order": {"payment:success"}, "call:save_address": {"msg:text"},
                "call:add_to_cart": {"btn:add"}, "call:save_size": {"btn:size"},
                "call:notify_calendar": {"btn:confirm"}, "call:reserve_slot": {"btn:confirm"},
                "call:create_ticket": {"msg:text"}, "call:log_message": {"msg:text"}}
        for i, x in list(enumerate(self.lines)):
            on = x.get("on") if x.get("ctl") == "crash" else None
            if not on or on == "send" or on.startswith("timer:"):
                continue
            evs = trig.get(on, {on})
            later = [j for j in range(i + 1, len(self.lines)) if self.lines[j].get("event") in evs]
            if later:
                continue
            idx = [j for j in range(len(self.lines)) if self.lines[j].get("event") in evs]
            if not idx:
                raise SystemExit(f"{self.name}: crash filter {on} matches nothing")
            self.lines.pop(i)
            self.lines.insert(idx[-1] - 1 if idx[-1] > i else idx[-1], x)

    def write(self):
        self.fix_crash_filters()
        os.makedirs(OUT, exist_ok=True)
        with open(os.path.join(OUT, self.name + ".jsonl"), "w") as f:
            for x in self.lines:
                f.write(json.dumps(x, ensure_ascii=False, separators=(",", ":")) + "\n")
        return self.name, len(self.lines)


# ------------------------------------------------------------------ pizza paths
def pz_happy(r, addr=None):
    return [("cmd:start", None), ("btn:order", None), ("btn:pizza", None), ("btn:size", r.choice(SIZES)),
            ("btn:add", None), ("btn:checkout", None), ("msg:text", addr or r.choice(ADDR_IN)),
            ("payment:success", None)]


def pz_variant(r, kind):
    s = r.choice(SIZES)
    a = r.choice(ADDR_IN)
    if kind == "happy":
        return pz_happy(r)
    if kind == "empty_cart":
        return [("cmd:start", None), ("btn:order", None), ("btn:cart", None), ("btn:checkout", None),
                ("btn:pizza", None), ("btn:size", s), ("btn:add", None), ("btn:checkout", None), ("msg:text", a),
                ("payment:success", None)]
    if kind == "out_of_zone":
        return pz_happy(r, r.choice(ADDR_OUT)) + [("payment:success", None)]
    if kind == "help":
        return [("cmd:start", None), ("btn:order", None), ("btn:pizza", None), ("btn:size", s), ("cmd:help", None),
                ("btn:back", None), ("btn:add", None), ("btn:checkout", None), ("msg:text", a),
                ("payment:success", None)]
    if kind == "cancel":
        return [("cmd:start", None), ("btn:order", None), ("btn:pizza", None), ("btn:size", s), ("btn:add", None),
                ("btn:cancel", None), ("btn:order", None), ("btn:pizza", None), ("btn:size", s), ("btn:add", None),
                ("btn:checkout", None), ("msg:text", a), ("payment:success", None)]
    if kind == "double_payment":
        return pz_happy(r) + [("payment:success", None), ("payment:success", None)]
    raise KeyError(kind)


def pz_life2(r):
    return [("btn:order", None), ("btn:pizza", None), ("btn:size", r.choice(SIZES)), ("btn:add", None),
            ("btn:checkout", None), ("msg:text", r.choice(ADDR_IN)), ("payment:success", None)]


# ------------------------------------------------------------------ scenario 1: order
def order(seed):
    g = Gen("order", seed, "pizza")
    r = g.rng
    kinds = ["happy", "empty_cart", "out_of_zone", "help", "cancel", "double_payment"]
    g1 = {f"u{i:02d}": pz_variant(r, kinds[i % len(kinds)]) for i in range(1, 11)}
    rem_users = ["u11", "u12"]           # wait at cart -> 2 h reminder -> resume
    for u in rem_users:
        g1[u] = [("cmd:start", None), ("btn:order", None), ("btn:pizza", None), ("btn:size", "M"), ("btn:add", None)]
    reset_users = ["u13", "u14"]         # order, "delete my data", order again (ET-01 path)
    for u in reset_users:
        g1[u] = pz_happy(r)
    faults1 = [
        {"ctl": "crash", "at": "before_commit"},
        {"ctl": "external", "target": "crm", "mode": "500", "count": 3},
        {"ctl": "crash", "at": "after_commit", "on": "payment:success"},
        {"ctl": "executor", "mode": "pause"},
        {"ctl": "external", "target": "tg", "mode": "timeout", "count": 2},
        {"ctl": "crash", "at": "before_effect", "on": "call:create_order"},
        {"ctl": "executor", "mode": "resume"},
        {"ctl": "external", "target": "crm", "mode": "timeout", "count": 2},
        {"ctl": "crash", "at": "after_effect_before_ack", "on": "call:create_order"},
    ]
    g.interleave(g1, dup_p=0.25, faults=faults1, delay_p=0.05)
    g.ctl(ctl="advance_ms", ms=7200000)
    for u in reset_users:
        g.ctl(ctl="reset_user", user=u)
    g2 = {u: [("btn:back", None), ("btn:checkout", None), ("msg:text", r.choice(ADDR_IN)), ("payment:success", None)]
          for u in rem_users}
    for u in reset_users:
        g2[u] = pz_life2(r)
    for i in range(15, 19):
        g2[f"u{i:02d}"] = pz_happy(r)
    faults2 = [
        {"ctl": "external", "target": "all", "mode": "reorder", "count": 6},
        {"ctl": "crash", "at": "after_effect_before_ack", "on": "send"},
        {"ctl": "external", "target": "crm", "mode": "429", "count": 2, "ms": 30},
        {"ctl": "crash", "at": "after_effect_before_ack", "on": "call:add_to_cart"},
        {"ctl": "external", "target": "crm", "mode": "dup", "count": 2},
        {"ctl": "external", "target": "tg", "mode": "dup", "count": 1},
        {"ctl": "crash", "at": "before_commit", "on": "btn:checkout"},
        {"ctl": "external", "target": "all", "mode": "delay", "count": 1, "ms": 1500},
        {"ctl": "restart_host"},
    ]
    g.interleave(g2, dup_p=0.25, faults=faults2)
    return g.write()


# ------------------------------------------------------------------ scenario 2: booking
def booking(seed):
    g = Gen("booking", seed, "booking")
    r = g.rng
    users = [f"b{i:02d}" for i in range(1, 19)]
    g.interleave({u: [("cmd:start", None), ("btn:book", None)] for u in users}, dup_p=0.2)
    g.ctl(ctl="crash", at="before_commit", on="btn:confirm")
    g.ctl(ctl="external", target="crm", mode="500", count=2)

    def burst(group, slot, fallback, with_crash=None):
        g.interleave({u: [("btn:slot", slot)] for u in group})
        if with_crash:
            g.ctl(**with_crash)
        g.ctl(ctl="barrier")
        order = list(group)
        r.shuffle(order)
        for u in order:   # consecutive lines for different users -> confirmations race in the host
            g.upd(u, "btn:confirm", dup=2 if r.random() < 0.3 else None)
        g.ctl(ctl="barrier")
        # symmetric follow-up: winners ignore retry/slot/confirm, losers re-book on the fallback slot
        g.interleave({u: [("btn:retry", None), ("btn:slot", fallback), ("btn:confirm", None), ("btn:my", None)]
                      for u in group})
        g.ctl(ctl="barrier")

    burst(users[0:4], "s1", "s4", {"ctl": "crash", "at": "after_commit", "on": "btn:confirm"})
    burst(users[4:8], "s2", "s4", {"ctl": "crash", "at": "after_effect_before_ack", "on": "call:notify_calendar"})
    burst(users[8:12], "s3", "s4", {"ctl": "crash", "at": "before_effect", "on": "call:reserve_slot"})
    g.ctl(ctl="external", target="crm", mode="timeout", count=2)
    burst(users[12:14], "s3", "s4")
    # hold timer expiry: b15 waits on confirm for 10 minutes
    g.upd("b15", "btn:slot", "s1")
    g.ctl(ctl="advance_ms", ms=600000)
    g.interleave({"b15": [("btn:retry", None), ("btn:cancel", None), ("btn:my", None)]})
    # cancellation frees capacity on s3 (b09 always holds s3: burst 3 had no contention)
    g.interleave({"b09": [("btn:my", None), ("btn:cancel_booking", None)]})
    g.ctl(ctl="barrier")   # documented: cancellations must be executed before others compete (rule G2)
    g.interleave({"b16": [("btn:slot", "s3"), ("btn:confirm", None), ("btn:my", None)],
                  "b17": [("btn:cancel", None), ("btn:my", None)],
                  "b18": [("btn:slot", "zz"), ("btn:confirm", None)]}, dup_p=0.3)
    g.ctl(ctl="restart_host")
    g.interleave({"b09": [("btn:my", None), ("btn:book", None), ("btn:slot", "s4"), ("btn:confirm", None)]})
    return g.write()


# ------------------------------------------------------------------ scenario 3: support
def support(seed):
    g = Gen("support", seed, "support")
    r = g.rng
    g.interleave({"s03": [("cmd:start", None), ("btn:support", None), ("btn:billing", None), ("msg:text", "bill wrong")]})
    g.ctl(ctl="advance_ms", ms=3600000)                       # SLA timer -> escalation (guarded timer arrow)
    g.interleave({"s03": [("msg:text", "operator?"), ("btn:resolved", None)]})
    p1 = {
        "s01": [("cmd:start", None), ("btn:support", None), ("btn:billing", None), ("msg:text", "invoice 17"),
                ("msg:text", "attached"), ("cmd:help", None), ("btn:back", None), ("msg:text", "still there?"),
                ("btn:escalate", None), ("msg:text", "hello operator"), ("btn:resolved", None)],
        "s02": [("cmd:start", None), ("btn:support", None), ("btn:tech", None), ("btn:escalate", None),
                ("msg:text", "app crashes"), ("btn:escalate", None), ("btn:escalate", None), ("btn:close", None)],
        "s04": [("cmd:start", None), ("btn:support", None), ("btn:billing", None), ("msg:text", "refund"),
                ("cmd:help", None)],                           # in faq during the upgrade, resume -> b_wait (deleted)
        "s05": [("cmd:start", None), ("btn:support", None), ("btn:billing", None), ("msg:text", "double charge")],
        "s06": [("cmd:start", None), ("btn:support", None), ("btn:tech", None), ("msg:text", "login"),
                ("btn:escalate", None)],                       # at human during the upgrade
        "s07": [("cmd:start", None), ("btn:support", None), ("btn:close", None)],   # close without ticket -> home
    }
    faults = [{"ctl": "external", "target": "crm", "mode": "500", "count": 3},
              {"ctl": "crash", "at": "after_effect_before_ack", "on": "call:create_ticket"},
              {"ctl": "external", "target": "crm", "mode": "429", "count": 2, "ms": 20},
              {"ctl": "crash", "at": "before_effect", "on": "call:log_message"},
              {"ctl": "crash", "at": "after_commit", "on": "btn:escalate"},
              {"ctl": "external", "target": "crm", "mode": "timeout", "count": 2}]
    g.interleave(p1, dup_p=0.3, faults=faults)
    g.ctl(ctl="upgrade", bot="support.v2")
    p2 = {
        "s04": [("btn:back", None), ("btn:billing", None), ("msg:text", "refund again"), ("msg:text", "pls")],
        "s05": [("msg:text", "hello?"), ("btn:support", None), ("btn:billing", None), ("msg:text", "double charge 2")],
        "s06": [("msg:text", "any news"), ("btn:resolved", None), ("btn:new", None)],
        "s08": [("cmd:start", None), ("btn:support", None), ("btn:billing", None), ("msg:text", "v2 user")],
        "s09": [("cmd:start", None), ("btn:support", None), ("btn:tech", None), ("msg:text", "bug"),
                ("msg:text", "more"), ("btn:escalate", None), ("btn:resolved", None)],
    }
    g.interleave(p2, dup_p=0.3, faults=[{"ctl": "crash", "at": "after_effect_before_ack", "on": "call:log_message"},
                                        {"ctl": "restart_host"}])
    g.ctl(ctl="advance_ms", ms=1800000)                       # sla2 on v2 for users waiting in b_pending
    g.interleave({"s08": [("msg:text", "thanks"), ("btn:resolved", None)]})
    return g.write()


# ------------------------------------------------------------------ scenario 4: reminders
def reminders(seed):
    g = Gen("reminders", seed, "reminders")
    r = g.rng
    texts = {"r01": "call mom", "r02": "", "r03": "!pay rent", "r04": "water plants", "r05": "gym",
             "r06": "stretch", "r07": "meds", "r08": "standup", "r09": "!deploy", "r10": "lunch"}
    g.ctl(ctl="timer_mode", ignore_cancel=True)                 # cancels do not stop in-flight fires
    g.interleave({u: [("cmd:start", None), ("btn:set", t)] for u, t in texts.items()}, dup_p=0.2)
    g.ctl(ctl="advance_ms", ms=30000)                           # t=30s
    g.interleave({"r04": [("btn:snooze", None)], "r05": [("btn:cancel", None)]})
    g.ctl(ctl="reset_user", user="r09")                         # timers pending; restart must not reuse tokens
    g.interleave({"r09": [("btn:set", "!deploy v2")]})
    g.ctl(ctl="crash", at="before_commit", on="timer:remind")
    g.ctl(ctl="advance_ms", ms=15000)                           # t=45s: nag fires (guarded timer arrow, no else)
    g.ctl(ctl="timer_mode", dup=2)                              # double callbacks
    g.ctl(ctl="advance_ms", ms=15000)                           # t=60s: remind fires (guarded, with else)
    g.ctl(ctl="timer_mode", dup=1)
    g.interleave({"r01": [("btn:ack", None)], "r06": [("btn:snooze", None)]})
    g.ctl(ctl="restart_host")                                   # pending timers must survive the restart
    g.ctl(ctl="advance_ms", ms=30000)                           # t=90s: autoclose, r04's re-armed remind
    g.ctl(ctl="replay_stale", user="r04")
    g.ctl(ctl="replay_stale", user="r05")
    g.ctl(ctl="external", target="crm", mode="500", count=2)
    g.ctl(ctl="advance_ms", ms=60000)                           # t=150s
    g.ctl(ctl="timer_mode", ignore_cancel=False)
    g.interleave({u: [("cmd:start", None), ("btn:set", "again " + u), ("btn:snooze", None)] for u in ("r02", "r07", "r08")},
                 dup_p=0.3)
    g.ctl(ctl="advance_ms", ms=120000)
    g.ctl(ctl="replay_stale", user="r09")
    return g.write()


# ------------------------------------------------------------------ scenario 5: migration
def migration(seed):
    g = Gen("migration", seed, "pizza")
    r = g.rng
    pre = {
        "m01": [("cmd:start", None), ("btn:order", None), ("btn:pizza", None), ("btn:size", "L")],   # at pick_toppings
        "m02": [("cmd:start", None), ("btn:order", None), ("btn:pizza", None), ("btn:size", "S"), ("cmd:help", None)],
        "m03": [("cmd:start", None), ("btn:order", None), ("btn:pizza", None), ("btn:size", "M"), ("btn:add", None)],
        "m04": [("cmd:start", None), ("btn:order", None), ("btn:pizza", None), ("btn:size", "M"), ("btn:add", None),
                ("btn:checkout", None)],                                                           # at address
        "m05": pz_happy(r),                                                                        # done
        "m07": [("cmd:start", None), ("btn:order", None), ("btn:cart", None)],                     # cart, timer
        "m08": [("cmd:start", None), ("btn:order", None), ("btn:pizza", None)],                    # pick_size
    }
    g.interleave(pre, dup_p=0.2)
    g.ctl(ctl="upgrade", bot="pizza.v2")
    post = {
        "m01": [("btn:add", None), ("btn:order", None), ("btn:pizza", None), ("btn:size", "L"), ("btn:add", None),
                ("btn:checkout", None), ("msg:text", "Mira 2"), ("payment:success", None)],
        "m02": [("btn:back", None), ("btn:pizza", None), ("btn:add", None), ("btn:checkout", None),
                ("msg:text", "Lenina 3"), ("payment:success", None)],
        "m04": [("msg:text", "Gagarina 1"), ("payment:success", None)],
        "m05": [("cmd:start", None)] + pz_life2(r),
        "m06": [("cmd:start", None), ("btn:order", None), ("btn:pizza", None), ("btn:size", "S"), ("btn:size", "L"),
                ("btn:add", None), ("btn:checkout", None), ("msg:text", "Pushkina 9"), ("payment:success", None)],
        "m08": [("btn:size", "S"), ("btn:add", None), ("btn:checkout", None), ("msg:text", "Mira 8"),
                ("payment:success", None)],
    }
    g.interleave(post, dup_p=0.2, faults=[{"ctl": "crash", "at": "after_commit"},
                                          {"ctl": "crash", "at": "after_effect_before_ack", "on": "call:create_order"}])
    g.ctl(ctl="advance_ms", ms=7200000)          # m03/m07 cart_reminder was renamed in v2: orphaned, never fires
    g.interleave({"m03": [("btn:checkout", None), ("msg:text", "Lenina 9"), ("payment:success", None)],
                  "m07": [("btn:checkout", None), ("btn:pizza", None), ("btn:add", None), ("btn:checkout", None),
                          ("msg:text", "Mira 1"), ("payment:success", None)]})
    return g.write()


# ------------------------------------------------------------------ scenario 6: failures
def failures(seed):
    g = Gen("failures", seed, "pizza", engine_proxy=True)
    r = g.rng
    scripts = {f"f{i:02d}": pz_variant(r, ["happy", "empty_cart", "help", "cancel"][i % 4]) for i in range(1, 15)}
    faults = []
    for at, on in [("before_commit", None), ("after_commit", None), ("before_effect", None),
                   ("after_effect_before_ack", None), ("before_commit", "payment:success"),
                   ("after_commit", "btn:add"), ("before_effect", "call:create_order"),
                   ("after_effect_before_ack", "call:create_order"), ("after_effect_before_ack", "send"),
                   ("after_effect_before_ack", "call:save_address")]:
        faults.append({"ctl": "crash", "at": at, **({"on": on} if on else {})})
    faults += [{"ctl": "db_lock", "ms": 5000}, {"ctl": "db_lock", "ms": 5000},
               {"ctl": "engine_fault", "mode": "kill"}, {"ctl": "engine_fault", "mode": "kill"},
               {"ctl": "engine_fault", "mode": "hang"}, {"ctl": "engine_fault", "mode": "corrupt"},
               {"ctl": "engine_fault", "mode": "truncate"},
               {"ctl": "external", "target": "crm", "mode": "500", "count": 5},
               {"ctl": "external", "target": "crm", "mode": "429", "count": 3, "ms": 25},
               {"ctl": "external", "target": "crm", "mode": "timeout", "count": 3},
               {"ctl": "external", "target": "crm", "mode": "timeout_before", "count": 2},
               {"ctl": "external", "target": "tg", "mode": "500", "count": 3},
               {"ctl": "external", "target": "tg", "mode": "timeout", "count": 2},
               {"ctl": "external", "target": "all", "mode": "delay", "count": 2, "ms": 1500},
               {"ctl": "restart_host"}]
    r.shuffle(faults)
    g.interleave(scripts, dup_p=0.3, faults=faults, delay_p=0.05)
    return g.write()


# ------------------------------------------------------------------ scenario 7: load
def load(n_users, seed=7):
    g = Gen(f"load_u{n_users}", seed, "pizza")
    r = g.rng
    g.interleave({f"L{i:04d}": pz_happy(r) for i in range(n_users)})
    return g.write()


# ------------------------------------------------------------------ scenario 8: adversarial
def adversarial(seed):
    g = Gen("adversarial", seed, "pizza", engine_proxy=True)
    r = g.rng
    normal = {f"a{i:02d}": pz_happy(r) for i in range(1, 7)}
    modes = ["garbage", "not_object", "unknown_field", "bad_version", "wrong_type", "missing_at", "huge_seq",
             "rollback", "deleted_node", "null"]
    targets = {f"c{i:02d}": m for i, m in enumerate(modes, 1)}
    pre = {u: pz_happy(r)[:5] for u in targets}                 # every target sits at cart (timer pending)
    pre["a03"] = pz_happy(r)[:5]                                # a03 at cart: token of cart_reminder = 5
    g.interleave(pre)
    g.ctl(ctl="barrier")
    for u, m in targets.items():
        g.ctl(ctl="corrupt_snapshot", user=u, mode=m)
    bad = [
        [1, 2, 3], "just a string", None, 42,
        {"user": "a01", "update_id": "77", "event": "btn:order"},
        {"user": "a01", "update_id": -5, "event": "btn:order"},
        {"user": "a01", "update_id": 1.5, "event": "btn:order"},
        {"user": "a01", "update_id": True, "event": "btn:order"},
        {"user": "a01", "update_id": 9007199254740992, "event": "btn:order"},
        {"user": "", "update_id": 9001, "event": "btn:order"},
        {"user": "a b", "update_id": 9002, "event": "btn:order"},
        {"user": "x" * 65, "update_id": 9003, "event": "btn:order"},
        {"user": 42, "update_id": 9004, "event": "btn:order"},
        {"user": "a02", "update_id": 9005},
        {"user": "a02", "update_id": 9006, "event": ""},
        {"user": "a02", "update_id": 9007, "event": 5},
        {"user": "a03", "update_id": 9008, "event": "timer:cart_reminder", "token": 5},   # forged timer fire
        {"user": "a03", "update_id": 9009, "event": "timer:cart_reminder"},
        {"user": "a03", "update_id": 9010, "event": "btn:checkout", "token": 5},
        {"user": "a02", "update_id": 9011, "event": "BTN:ORDER"},
        {"user": "a02", "update_id": 9012, "event": "btn:" + "x" * 100},
        {"user": "a02", "update_id": 9013, "event": "btn:order\n{\"id\":1,\"op\":\"start\"}"},
        {"user": "a02", "update_id": 9014, "event": "btn:order", "arg": 123},
        {"user": "a02", "update_id": 9015, "event": "msg:text", "arg": "x" * 5000},
        {"user": "a02", "update_id": 9016, "event": "msg:text", "arg": "a\u0000b"},
    ]
    seq = []
    for b in bad:
        seq.append(("raw", b))
    seq.append(("raw_text", "{not json"))
    seq.append(("raw_text", ""))
    seq.append(("raw_text", '{"user":"a02","user":"a05","update_id":9018,"event":"btn:order"}'))
    # unknown extra fields are accepted (Telegram adds fields) but must not inject state
    seq.append(("raw_noeffect", {"user": "a04", "update_id": 9017, "event": "btn:nothing",
                                 "snapshot": {"at": "done"}, "token": None}))
    efaults = [{"ctl": "engine_fault", "mode": m} for m in
               ("corrupt", "wrong_id", "truncate", "garbage", "bad_snapshot", "bad_action", "hang", "kill")]
    post = {u: [("btn:checkout", None), ("btn:order", None), ("btn:pizza", None), ("btn:size", "M"), ("btn:add", None),
                ("btn:checkout", None), ("msg:text", "Mira 3"), ("payment:success", None)] for u in targets}
    post.update(normal)
    post["a03"] = [("btn:checkout", None), ("msg:text", "Lenina 4"), ("payment:success", None)]
    # raw lines and engine faults are mixed into normal traffic
    rem = {u: list(s) for u, s in post.items()}
    extra = seq + [("ctl", f) for f in efaults]
    r.shuffle(extra)
    total = sum(len(s) for s in rem.values())
    pos = sorted(r.sample(range(total), len(extra)))
    k = j = 0
    while any(rem.values()):
        while j < len(extra) and pos[j] <= k:
            kind, body = extra[j]
            if kind == "raw":
                g.raw(body)
            elif kind == "raw_text":
                g.lines.append({"raw_text": body})
            elif kind == "raw_noeffect":
                body = dict(body)
                body.pop("token")
                g.lines.append({"raw": body, "expect": "no_effect"})
            else:
                g.ctl(**body)
            j += 1
        u = r.choice(sorted(x for x in rem if rem[x]))
        ev, arg = rem[u].pop(0)
        g.upd(u, ev, arg)
        k += 1
    return g.write()


def main():
    made = []
    for s in (1, 2):
        made += [order(s), booking(s), reminders(s), failures(s)]
    made += [support(1), migration(1), adversarial(1)]
    for n in (1, 10, 100, 1000):
        made.append(load(n))
    for name, n in made:
        print(f"{name}: {n} lines")


if __name__ == "__main__":
    main()

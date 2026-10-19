"""Targeted protocol scenarios for figram-engine (engine-timers audit).

    python3 ptdriver.py ENGINE suite EXAMPLES_DIR

Every scenario prints one JSON record:
  {"id", "area", "verdict": PASS|FAIL|OBS, "claim", "evidence"}
PASS/FAIL = hard check of documented behaviour (FORMAT.md §7, HOST.md);
OBS = recorded engine behaviour that the docs do not pin down (input for REPORT.md).
Exit code 1 if any hard check FAILs (or no scenario ran).
"""

import copy
import json
import os
import resource
import signal
import subprocess
import tempfile
import time

from ptdriver import MAXINT, Engine, Ref, snap_from_json, snap_to_json

RESULTS = []
ENGINE = None
TMP = None


def rec(sid, area, verdict, claim, evidence):
    r = {"id": sid, "area": area, "verdict": verdict, "claim": claim, "evidence": evidence}
    RESULTS.append(r)
    print(json.dumps(r, ensure_ascii=False), flush=True)


def hard(sid, area, cond, claim, evidence):
    rec(sid, area, "PASS" if cond else "FAIL", claim, evidence)


class E:
    """Engine session with parsed replies."""

    def __init__(self, doc, mutate=None):
        self.e = Engine(ENGINE, doc, mutate)

    def __call__(self, **req):
        raw, rid = self.e.call(req)
        if raw == b"<TIMEOUT>":
            return {"TIMEOUT": True}
        r = json.loads(raw)
        assert r.get("id") == rid, (raw, rid)
        return r

    def raw(self, data, expect=True, timeout=None):
        return self.e.send_raw(data, expect, timeout)

    def close(self):
        return self.e.close()


def write_doc(name, doc):
    p = os.path.join(TMP, name)
    with open(p, "w") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
    return p


def step(e, snap, ev, token=None, guards=None):
    req = {"op": "step", "snapshot": snap, "event": ev}
    if token is not None:
        req["token"] = token
    if guards is not None:
        req["guards"] = guards
    return e(**req)


def run_path(e, path):
    """path: list of (event, guards) for named events. Returns list of replies (results)."""
    r = e(op="start")
    out = [r]
    for ev, g in path:
        r = step(e, r["snapshot"], ev, guards=g)
        out.append(r)
    return out


# ----------------------------------------------------------------------------

def need_scenarios(ex, own):
    pizza = os.path.join(ex, "pizza.bot.json")
    tneed = os.path.join(own, "t_need.bot.json")
    e = E(pizza)
    rs = run_path(e, [("btn:order", None), ("btn:cart", None)])
    cart = rs[-1]["snapshot"]
    r1 = step(e, cart, "btn:checkout")
    hard("N1", "need", r1.get("need") == ["cart_not_empty"], "missing guard -> need lists it", r1)
    r1b = step(e, cart, "btn:checkout")
    hard("N5", "need", r1b == dict(r1, id=r1b["id"]), "repeated need is identical (no state)", r1b)
    r2 = step(e, cart, "btn:checkout", guards={"cart_not_empty": True})
    r3 = step(e, cart, "btn:checkout", guards={"cart_not_empty": True, "zzz": False, "in_delivery_zone": True})
    hard("N3", "need", r2["snapshot"] == r3["snapshot"] and r2["actions"] == r3["actions"],
         "extra guards (unknown / irrelevant) do not change the result", {"plain": r2, "extra": r3})
    r4 = step(e, cart, "btn:checkout", guards={"cart_not_empty": 1})
    r5 = step(e, cart, "btn:checkout", guards={"cart_not_empty": True, "unrelated": None})
    r6 = step(e, cart, "btn:checkout", guards=[True])
    hard("N4", "need", all("error" in x and "snapshot" not in x for x in (r4, r5, r6)),
         "non-bool guard (relevant or not) / non-object guards -> error without snapshot", [r4, r5, r6])
    # need precedence: stale timer => ignored without need; deleted state => recovery without need
    r7 = step(e, dict(cart, timers={"cart_reminder": 1}), "timer:cart_reminder", token=999)
    hard("N8", "need", r7.get("actions") == [] and "need" not in r7,
         "stale timer event: ignored before guards are requested", r7)
    r8 = step(e, {"at": "deleted_msg", "seq": 5}, "btn:checkout")
    hard("N9", "need", r8.get("recovered") is True and "need" not in r8,
         "deleted current message: recovery, no need", r8)
    e.close()

    e = E(tneed)
    s = e(op="start")["snapshot"]          # at a
    ra = step(e, s, "btn:go")
    rt = step(e, s, "btn:go", guards={"expensive": True})
    rf = step(e, s, "btn:go", guards={"expensive": False})
    rec("N6", "need", "OBS",
        "need asks for a guard of an UNREACHABLE branch: own unconditional branch at 'a' shadows the "
        "section's 'if expensive'; result is identical for true/false",
        {"need": ra, "true": rt["snapshot"], "false": rf["snapshot"],
         "independent": rt["snapshot"] == rf["snapshot"] and rt["actions"] == rf["actions"]})
    hard("N6b", "need", rt["snapshot"] == rf["snapshot"] and rt["actions"] == rf["actions"],
         "guards_irrelevant: unreachable guard does not influence the result", None)
    m2 = step(e, s, "btn:m2", guards={})["snapshot"]
    rp = step(e, m2, "btn:pick")
    rec("N7", "need", "OBS", "within one transition a branch after an unconditional one is still requested "
        "(and `figram check` reports nothing)", rp)
    m3 = step(e, s, "btn:m3")["snapshot"]
    rq = step(e, m3, "btn:go", guards={"g1": True})
    rec("N2", "need", "OBS", "partial guards: g1=true already decides (first branch wins) but engine still "
        "asks for 'expensive' (all candidate guards are demanded at once)", rq)
    e.close()


def timer_scenarios(ex, own):
    tt = os.path.join(own, "t_timers.bot.json")
    e = E(tt)
    st = e(op="start")
    s1 = st["snapshot"]                                   # wait, tick:1, timeout:1
    hard("T0", "timers", s1.get("timers") == {"timeout": 1, "tick": 1} or s1.get("timers") == {"tick": 1, "timeout": 1},
         "two timers on one message share token = seq", st)
    # T10: shared token is per name -> timeout with tick's token is fine; foreign names ignored
    r = step(e, s1, "timer:zzz", token=1)
    r2 = step(e, s1, "timer:t", token=1)        # timer of another message
    r3 = step(e, s1, "timer:", token=1)          # empty id
    hard("T2", "timers", all(x.get("actions") == [] and x.get("snapshot") == s1 for x in (r, r2, r3)),
         "token for unknown / other message's / empty timer name: ignored, snapshot unchanged", [r, r2, r3])
    # T3: token boundary
    outs = {}
    for label, tokraw in [("2^53+1", str(2 ** 53 + 1)), ("2^64", str(2 ** 64)), ("1e18", "1e18"),
                          ("1e19", "1e19"), ("1.0", "1.0"), ("1.5", "1.5"), ("-0", "-0"), ("\"1\"", "\"1\""),
                          ("10e-1", "10e-1"), ("-1", "-1")]:
        line = ('{"id":"%s","op":"step","snapshot":%s,"event":"timer:tick","token":%s}\n'
                % (label.replace('"', ''), json.dumps(s1), tokraw)).encode()
        outs[label] = json.loads(e.raw(line))
    big_ok = all(outs[k].get("actions") == [] and outs[k].get("snapshot") == s1 for k in ("2^53+1", "2^64", "1e18"))
    hard("T3", "timers", big_ok, "token > 2^53-1 accepted syntactically, never matches, ignored (no error)",
         {k: outs[k] for k in ("2^53+1", "2^64", "1e18")})
    rec("T3b", "timers", "OBS", "token spelling: 1.0 / 10e-1 equal 1 and FIRE the timer; 1e19 rejected as "
        "'not a non-negative integer' (numToNat e>18), 1.5/-1/\"1\" -> error; -0 = 0",
        {k: (outs[k].get("error") or ("fired" if outs[k].get("actions") else "ignored")) for k in outs})
    # T4: replay of the same fire twice + self-loop restarting BOTH timers
    f1 = step(e, s1, "timer:tick", token=1)
    s2 = f1["snapshot"]
    f2 = step(e, s2, "timer:tick", token=1)
    hard("T4", "timers", f1["actions"][-1] == {"send": "wait"} and f2["actions"] == [] and f2["snapshot"] == s2,
         "replayed fire (same token) after the transition is ignored", {"first": f1, "replay": f2})
    f3 = step(e, s2, "timer:timeout", token=1)
    hard("T10", "timers", f3["actions"] == [], "timeout's old shared token is stale after tick's self-loop", f3)
    # periodic self-loop starves sibling timer: simulate 20 ticks with host scheduler times
    s = s1
    timeout_fired = False
    for i in range(20):
        r = step(e, s, "timer:tick", token=s["timers"]["tick"])
        s = r["snapshot"]
        if s["at"] != "wait":
            timeout_fired = True
    rec("T4b", "timers", "OBS", "self-loop on timer 'tick' (1 s) re-enters 'wait' and restarts 'timeout' (10 s) "
        "too: with ticks every 1 s the 10 s timeout can never fire (spec decision 3 consequence)",
        {"ticks": 20, "timeout_ever_due": timeout_fired, "timers_after": s.get("timers"), "seq": s.get("seq"),
         "actions_per_tick": r["actions"]})
    # T5: timer without arrow stays in snapshot after its (accepted) fire
    idle = step(e, s1, "btn:idle")["snapshot"]
    fi = step(e, idle, "timer:t", token=idle["timers"]["t"])
    fi2 = step(e, fi["snapshot"], "timer:t", token=idle["timers"]["t"])
    rec("T5", "timers", "OBS", "fire of a timer with no arrow: accepted, ignored, timer STAYS in snapshot.timers "
        "(host scheduler no longer has it); replay is accepted again (no-op)", {"fire": fi, "replay": fi2})
    # T6: guarded timer arrow, guard false -> timer stays; later delivery with guard true fires
    g = step(e, s1, "btn:guarded")["snapshot"]
    tok = g["timers"]["gt"]
    n1 = step(e, g, "timer:gt", token=tok)
    gf = step(e, g, "timer:gt", token=tok, guards={"ok": False})
    gt = step(e, gf["snapshot"], "timer:gt", token=tok, guards={"ok": True})
    rec("T6", "timers", "OBS", "timer fire whose guard is false is ignored but the SAME token stays valid: a second "
        "delivery of the same fire with ok=true transitions (protected only by host update_id dedup)",
        {"need": n1, "false": gf, "second_delivery_true": gt})
    # T11: same timer name on two messages, transition by that timer
    t1 = step(e, s1, "btn:twin")["snapshot"]
    a = step(e, t1, "timer:same", token=t1["timers"]["same"])
    b = step(e, a["snapshot"], "timer:same", token=t1["timers"]["same"])
    hard("T11", "timers", a["snapshot"]["at"] == "twin2" and b["actions"] == []
         and a["actions"][0] == {"cancel_timer": "same"} and a["actions"][1]["start_timer"] == "same",
         "same timer name on consecutive messages: cancel+start with new token; old token ignored", [a, b])
    c = step(e, s1, "btn:colon")["snapshot"]
    cc = step(e, c, "timer:a:b", token=c["timers"]["a:b"])
    hard("T12", "timers", cc["snapshot"]["at"] == "gone", "timer name containing ':'", cc)
    e.close()

    # T8: cancel/expire race on pizza cart, both delivery orders
    pizza = os.path.join(ex, "pizza.bot.json")
    e = E(pizza)
    rs = run_path(e, [("btn:order", None), ("btn:cart", None)])
    cart = rs[-1]["snapshot"]
    tok = cart["timers"]["cart_reminder"]
    # order A: timer first, then click
    a1 = step(e, cart, "timer:cart_reminder", token=tok)
    a2 = step(e, a1["snapshot"], "btn:checkout", guards={"cart_not_empty": True})
    # order B: click first (cancel_timer emitted), then the in-flight timer event
    b1 = step(e, cart, "btn:checkout", guards={"cart_not_empty": True})
    b2 = step(e, b1["snapshot"], "timer:cart_reminder", token=tok)
    hard("T8", "timers", {"cancel_timer": "cart_reminder"} in b1["actions"] and b2["actions"] == []
         and b2["snapshot"] == b1["snapshot"],
         "cancel/expire race: event delivered after cancel_timer is ignored (cancel is an optimisation)",
         {"A": [a1["snapshot"], a2.get("snapshot")], "B": [b1, b2]})
    e.close()

    # T9: self-loop on a button restarts the timer with a new token (features doc)
    e = E(os.path.join(ex, "features.bot.json"))
    h = e(op="start")["snapshot"]
    s2 = step(e, h, "btn:self")
    old = step(e, s2["snapshot"], "timer:idle", token=h["timers"]["idle"])
    hard("T9", "timers", s2["actions"][0] == {"cancel_timer": "idle"} and s2["snapshot"]["timers"]["idle"] == 2
         and old["actions"] == [], "button self-loop: cancel+restart timer, old token ignored", [s2, old])
    e.close()


def seq_scenarios(ex, own):
    pizza = os.path.join(ex, "pizza.bot.json")
    e = E(pizza)
    a = step(e, {"at": "menu", "seq": MAXINT - 1}, "btn:order")
    b = step(e, {"at": "menu", "seq": MAXINT}, "btn:order")
    c = step(e, {"at": "menu", "seq": MAXINT}, "btn:nothing")
    d = e(op="restart", snapshot={"at": "menu", "seq": MAXINT})
    f = step(e, {"at": "menu", "seq": MAXINT + 1}, "btn:order")
    g = step(e, {"at": "deleted", "seq": MAXINT}, "btn:order")
    hard("S1", "seq", a.get("snapshot", {}).get("seq") == MAXINT and b.get("error") == "seq_overflow"
         and c.get("actions") == [] and d.get("error") == "seq_overflow" and "error" in f,
         "seq boundary 2^53-1: last step ok, next -> seq_overflow; ignored event ok; restart -> seq_overflow; "
         "seq 2^53 rejected", {"maxint-1": a, "maxint": b, "ignored": c, "restart": d, "2^53": f})
    rec("S1b", "seq", "OBS", "at seq=2^53-1 with a deleted message every request errors (recovery needs a "
        "step); only `start` (seq reset) gets the user out", g)

    # S2: start vs restart: idempotency-key collisions (HOST.md §1.4 key = user:seq:index)
    order_path = [("btn:order", None), ("btn:cart", None), ("btn:checkout", {"cart_not_empty": True}),
                  ("msg:text", {"in_delivery_zone": True}), ("payment:success", None)]

    def keys_of(replies, user="u"):
        out = {}
        for r in replies:
            seq = r["snapshot"].get("seq", 0)
            for i, act in enumerate(r["actions"]):
                out[f"{user}:{seq}:{i}"] = act
        return out
    life1 = run_path(e, order_path)
    k1 = keys_of(life1)
    # reset via start (HOST.md §4 allows it when the snapshot is lost / "delete my data")
    life2 = run_path(e, order_path)
    k2 = keys_of(life2)
    coll = {k: (k1[k], v) for k, v in k2.items() if k in k1}
    # reset via restart
    r = e(op="restart", snapshot=life1[-1]["snapshot"])
    life3 = [r]
    for ev, gd in order_path:
        r = step(e, r["snapshot"], ev, guards=gd)
        life3.append(r)
    k3 = keys_of(life3)
    coll3 = {k: v for k, v in k3.items() if k in k1}
    co1 = [k for k, v in k1.items() if v == {"call": "create_order"}]
    co2 = [k for k, v in k2.items() if v == {"call": "create_order"}]
    hard("S2", "seq", len(coll3) == 0, "restart continues seq: no key collides with the previous life", {"collisions": coll3})
    rec("S2b", "seq", "OBS", "start after a lost/deleted snapshot reuses keys user:seq:index: the 2nd order's "
        "create_order/save_address/send get EXACTLY the keys of the 1st order -> idempotent CRM/outbox drops them",
        {"life1_create_order_key": co1, "life2_create_order_key": co2,
         "colliding_keys": len(coll), "life2_keys": len(k2),
         "collision_rate": f"{len(coll)}/{len(k2)}", "examples": dict(list(coll.items())[:8])})

    # quantified over random second lives (engine-driven): share of life-2 actions whose key existed in life 1
    import random
    rnd = random.Random(7)
    ref = Ref(json.load(open(pizza)))
    tot = hit = calls = call_hit = same_call_hit = 0
    for trial in range(60):
        lives = []
        for life in range(2):
            r = e(op="start")
            reps = [r]
            for _ in range(40):
                s = r["snapshot"]
                evs = [x for x in ref.events if ref.cands(s["at"], x) and not x.startswith("timer:")]
                ev = rnd.choice(evs)
                gd = {gname: rnd.random() < 0.7 for gname in ref.required(snap_from_json(s), ev, None)}
                r = step(e, s, ev, guards=gd)
                reps.append(r)
            lives.append(keys_of(reps))
        a1, a2 = lives
        for k, v in a2.items():
            tot += 1
            hit += k in a1
            if "call" in v:
                calls += 1
                call_hit += k in a1
                same_call_hit += a1.get(k) == v
    rec("S2c", "seq", "OBS", "60 random user pairs (life1 40 steps, start-reset, life2 40 steps) via the engine",
        {"life2_actions": tot, "key_collides_with_life1": hit, "rate": round(hit / tot, 3),
         "life2_calls": calls, "call_key_collides": call_hit, "call_key_collides_same_call": same_call_hit})

    # S3: restart from a STALE snapshot -> keys and timer tokens of the newer life are reused
    p = [("btn:order", None), ("btn:cart", None), ("btn:checkout", {"cart_not_empty": False}), ("btn:cart", None)]
    lifeA = run_path(e, p)           # seq 1..5, cart_reminder token 5 scheduled at the end
    kA = keys_of(lifeA)
    stale = lifeA[1]["snapshot"]     # seq 2, catalog, no timers (e.g. read from a lagging replica)
    r = e(op="restart", snapshot=stale)
    lifeB = [r]
    for ev, gd in [("btn:order", None), ("btn:cart", None)]:
        r = step(e, r["snapshot"], ev, guards=gd)
        lifeB.append(r)
    kB = keys_of(lifeB)
    collB = sorted(k for k in kB if k in kA)
    tokA = lifeA[-1]["snapshot"]["timers"]["cart_reminder"]
    tokB = lifeB[-1]["snapshot"]["timers"]["cart_reminder"]
    late = step(e, lifeB[-1]["snapshot"], "timer:cart_reminder", token=tokA)
    rec("S3", "seq", "OBS", "restart from a stale snapshot (not the latest committed one): restart cannot cancel "
        "timers it does not see, keys collide and the OLD in-flight cart_reminder token equals the new one -> "
        "accepted early", {"colliding_keys": collB, "old_token": tokA, "new_token": tokB,
                           "late_old_event": late.get("snapshot", {}).get("at"), "restart_actions": lifeB[0]["actions"]})

    # S4/S5: restart with unknown snapshot; recovered semantics
    r1 = e(op="restart", snapshot={"at": "nope", "timers": {"ghost": 3}, "resume": {"x": "y"}, "seq": 9})
    r2 = e(op="restart", snapshot={"v": 2, "at": "menu"})
    r3 = e(op="restart", snapshot={"at": "menu", "foo": 1})
    hard("S4", "seq", r1.get("snapshot", {}).get("seq") == 10 and {"cancel_timer": "ghost"} in r1.get("actions", [])
         and "recovered" not in r1 and "error" in r2 and "error" in r3,
         "restart: unknown message/timers accepted (cancelled), seq continues; unknown version/field rejected",
         [r1, r2, r3])
    q1 = step(e, {"at": "nope", "seq": 3, "timers": {"ghost": 3}}, "timer:ghost", token=99)
    q2 = step(e, {"at": "order", "seq": 3}, "btn:cancel")       # a SECTION id as current message
    q3 = step(e, {"at": "menu", "seq": 3}, "btn:none")
    hard("S5", "seq", q1.get("recovered") is True and q2.get("recovered") is True and "recovered" not in q3
         and "recovered" not in r1,
         "recovered=true only for a step whose current message is not a message of the doc (also for a stale "
         "timer event / a section id); restart never sets it", [q1, q2, q3])
    e.close()


def upgrade_scenarios(ex, own):
    base = json.load(open(os.path.join(ex, "pizza.bot.json")))
    v1 = write_doc("v1.bot.json", base)
    e1 = E(v1)
    rs = run_path(e1, [("btn:order", None), ("btn:cart", None)])
    at_cart = rs[-1]["snapshot"]                          # cart, timers cart_reminder:3, resume order:cart
    rs2 = run_path(e1, [("btn:order", None), ("btn:pizza", None), ("btn:size", None), ("cmd:help", None)])
    at_help = rs2[-1]["snapshot"]                         # help, resume order:pick_toppings
    e1.close()
    order = lambda d: d["bot"]["children"]["order"]

    def variant(name, fn):
        d = copy.deepcopy(base)
        fn(d)
        p = write_doc(name + ".bot.json", d)
        chk = subprocess.run([ENGINE, "--mutants", p], capture_output=True, text=True, timeout=30)
        return p, chk.returncode, chk.stderr.strip()[:200]

    # U1 delete current message
    def del_cart(d):
        del order(d)["children"]["cart"]
        order(d)["children"]["catalog"]["on"]["btn:cart"] = [{"to": "catalog"}]
        order(d)["children"]["pizza"]["children"]["pick_toppings"]["on"]["btn:add"] = [{"to": "catalog", "do": ["add_to_cart"]}]
    p, rc, err = variant("v2_del_cart", del_cart)
    e = E(p)
    r = step(e, at_cart, "btn:checkout", guards={"cart_not_empty": True})
    hard("U1", "upgrade", r.get("recovered") is True and {"cancel_timer": "cart_reminder"} in r["actions"]
         and r["snapshot"].get("resume") == at_cart.get("resume"),
         "deleted current message: recovered=true, its timers cancelled, resume kept (HOST §6, FORMAT rule 1)",
         {"load_rc": rc, "reply": r})
    e.close()

    # U2 delete resume target / U3 move it out of the section
    def del_pt(d):
        pz = order(d)["children"]["pizza"]["children"]
        del pz["pick_toppings"]
        pz["pick_size"]["on"]["btn:size"] = [{"to": "cart", "do": ["save_size"]}]
    p, rc, err = variant("v2_del_resume_target", del_pt)
    e = E(p)
    r = step(e, at_help, "btn:back")
    hard("U2", "upgrade", r.get("snapshot", {}).get("at") == "catalog",
         "deleted resume target -> section's initial message (HOST §6)", {"load_rc": rc, "reply": r})
    e.close()

    def move_pt(d):
        pz = order(d)["children"]["pizza"]["children"]
        d["bot"]["children"]["pick_toppings"] = pz.pop("pick_toppings")
        pz["pick_size"]["on"]["btn:size"] = [{"to": "pick_toppings", "do": ["save_size"]}]
    p, rc, err = variant("v2_move_resume_target", move_pt)
    e = E(p)
    r = step(e, at_help, "btn:back")
    rec("U3", "upgrade", "OBS", "resume target still exists but moved out of the section -> also falls back to "
        "the section's initial message (docs only mention 'no longer exists')", {"load_rc": rc, "reply": r})
    e.close()

    # U4 rename the timer on the current message
    def ren_timer(d):
        c = order(d)["children"]["cart"]
        c["timers"] = {"cart_nudge": 7200000}
        c["on"]["timer:cart_nudge"] = c["on"].pop("timer:cart_reminder")
    p, rc, err = variant("v2_rename_timer", ren_timer)
    e = E(p)
    old_tok = at_cart["timers"]["cart_reminder"]
    fire_old = step(e, at_cart, "timer:cart_reminder", token=old_tok)
    leave = step(e, at_cart, "btn:checkout", guards={"cart_not_empty": True})
    s = leave["snapshot"]
    trail = [s]
    for ev, gd in [("msg:text", {"in_delivery_zone": True}), ("payment:success", None), ("cmd:start", None)]:
        s = step(e, s, ev, guards=gd)["snapshot"]
        trail.append(s)
    rec("U4", "upgrade", "OBS", "renamed timer on the current message: v1 timer 'cart_reminder' is never cancelled "
        "(exit cancels only v2 'cart_nudge'), stays in snapshot.timers through every later step; user currently "
        "on 'cart' never gets the v2 reminder; old fire is accepted (token matches) but has no arrow",
        {"load_rc": rc, "fire_old_timer": fire_old, "leave_actions": leave["actions"],
         "timers_after_each_step": [x.get("timers") for x in trail]})
    e.close()

    # U5 timer moved to another message + section-level arrow
    def move_timer(d):
        c = order(d)["children"]["cart"]
        del c["timers"]
        arrow = c["on"].pop("timer:cart_reminder")
        order(d)["children"]["address"]["timers"] = {"cart_reminder": 600000}
        order(d)["on"]["timer:cart_reminder"] = arrow
    p, rc, err = variant("v2_move_timer", move_timer)
    e = E(p)
    r = step(e, at_cart, "timer:cart_reminder", token=old_tok)
    rec("U5", "upgrade", "OBS", "timer moved from 'cart' to 'address' with the arrow lifted to section 'order': "
        "the v1 in-flight fire (token still in snapshot) is ACCEPTED while the user is on 'cart' and moves them "
        "to 'reminder' although v2 has no reminder on 'cart'", {"load_rc": rc, "reply": r})
    e.close()

    # U6 message turned into a section
    def msg_to_sec(d):
        c = order(d)["children"].pop("cart")
        order(d)["children"]["cart"] = {"initial": "cart_view", "children": {"cart_view": c}}
    p, rc, err = variant("v2_msg_to_sec", msg_to_sec)
    e = E(p)
    r = step(e, at_cart, "btn:checkout", guards={"cart_not_empty": True})
    rec("U6", "upgrade", "OBS", "current message became a section of the same name: treated as deleted -> "
        "recovery to the bot's initial message (not the section's initial), recovered=true",
        {"load_rc": rc, "reply": r})
    e.close()

    # U7 section turned into a message
    def sec_to_msg(d):
        pz = order(d)["children"]["pizza"]
        order(d)["children"]["pizza"] = {"on": {"btn:add": [{"to": "cart", "do": ["add_to_cart"]}]}}
    p, rc, err = variant("v2_sec_to_msg", sec_to_msg)
    e = E(p)
    ev1 = E(v1)
    s_pick = run_path(ev1, [("btn:order", None), ("btn:pizza", None)])[-1]["snapshot"]  # pick_size
    ev1.close()
    r = step(e, s_pick, "btn:size")
    r2 = step(e, at_help, "btn:back")
    rec("U7", "upgrade", "OBS", "section 'pizza' became a message: v1 users inside it are recovered to 'menu'; "
        "resume {order: pick_toppings} (no longer a member) falls back to 'catalog'",
        {"load_rc": rc, "inside": r, "resume": r2})
    e.close()

    # U8 guard renamed
    def ren_guard(d):
        d["conditions"]["has_items"] = d["conditions"].pop("cart_not_empty")
        order(d)["children"]["cart"]["on"]["btn:checkout"][0]["if"] = "has_items"
    p, rc, err = variant("v2_rename_guard", ren_guard)
    e = E(p)
    r = step(e, at_cart, "btn:checkout", guards={"cart_not_empty": True})
    r2 = step(e, at_cart, "btn:checkout", guards={"cart_not_empty": True, "has_items": False})
    rec("U8", "upgrade", "OBS", "renamed guard: v1-era host value is silently ignored as 'extra', engine asks "
        "need [has_items]; a host that defaults unknown guards to false sends the user to 'catalog'",
        {"load_rc": rc, "old_name_only": r, "unknown_as_false": r2.get("snapshot")})
    # U9 hello does not identify the document
    h1 = E(v1)
    hv1 = h1(op="hello")
    h1.close()
    hv2 = e(op="hello")
    rec("U9", "upgrade", "OBS", "hello does not identify the document version (only protocol/states): host cannot "
        "detect which document a snapshot was produced with", {"v1": hv1, "v2_rename_guard": hv2})
    e.close()


def error_scenarios(ex, own):
    pizza = os.path.join(ex, "pizza.bot.json")
    e = E(pizza)
    s = e(op="start")["snapshot"]
    outs = []
    noid = json.loads(e.raw(b'{"op":"hello"}\n'))
    hard("E2a", "errors", noid.get("id") is None and noid.get("protocol") == 1,
         "valid request without id -> answered with id null", noid)
    for line in [b"garbage\n", b"[1,2]\n", b'"str"\n', b'{"id":7}\n',
                 b'{"id":8,"id":9,"op":"hello"}\n', b'{"id":10,"op":"hello"} trailing\n',
                 b'{"id":11,"op":"step","snapshot":{"at":"menu"},"event":"btn:order","guards":{"a":true,"a":false}}\n',
                 b'{"id":12,"op":"step","snapshot":{"at":"menu","seq":-1},"event":"btn:order"}\n',
                 b'{"id":13,' + b'[' * 300 + b']' * 300 + b'}\n']:
        outs.append((line[:80].decode(errors="replace"), json.loads(e.raw(line))))
    null_ids = [o for o in outs if o[1].get("id") is None]
    hard("E1", "errors", all("error" in o[1] and "snapshot" not in o[1] for o in outs),
         "malformed requests -> error, never a snapshot", outs)
    rec("E2", "errors", "OBS", "id is null for: invalid JSON, non-object, missing id, DUPLICATE KEY anywhere "
        "(even in guards), trailing text, nesting >256; the request's id is lost although it is often "
        "syntactically present. Replies are strictly FIFO, so a sequential host can still correlate by order.",
        {"null_id_cases": [o[0] for o in null_ids]})
    ids = {}
    for raw in [b'{"id":-0,"op":"hello"}', b'{"id":1.50,"op":"hello"}', b'{"id":1e2,"op":"hello"}',
                b'{"id":18446744073709551617,"op":"hello"}', b'{"id":"x\\u0000y","op":"hello"}',
                b'{"id":{"k":[1,{"z":null}]},"op":"hello"}']:
        rep = e.raw(raw + b"\n")
        ids[raw.decode()] = rep.decode().split(',"protocol"')[0] + "}"
    rec("E2b", "errors", "OBS", "id echo is by value, not byte-exact (-0 -> 0); big ints exact", ids)
    # blank lines: no reply, next request still correlated
    for blank in [b"\n", b"   \n", b"\r\n", b"\t\n"]:
        e.raw(blank, expect=False)
    r = e(op="hello")
    hard("E3", "errors", r.get("protocol") == 1, "blank/whitespace lines get no reply; stream stays in sync", r)
    # invalid UTF-8 inside a string
    t0 = time.time()
    rep = e.raw(b'{"id":20,"op":"step","snapshot":{"at":"menu"},"event":"btn:\xff\xfe"}\n', timeout=10)
    rec("E7", "errors", "OBS", "invalid UTF-8 in a request line", {"reply": rep[:300].decode(errors="replace"),
                                                                   "alive": e(op="hello").get("protocol") == 1})
    # state unchanged after errors: same request as before gives same answer
    ok1 = step(e, s, "btn:order")
    hard("E1b", "errors", ok1.get("snapshot", {}).get("at") == "catalog", "old snapshot still works after errors", ok1)
    rc, err = e.close()
    hard("E6", "errors", rc == 0, "stdin EOF -> exit 0", {"rc": rc, "stderr": err[:200]})

    # partial last line without newline
    p = subprocess.run([ENGINE, pizza], input=b'{"id":1,"op":"hello"}', capture_output=True, timeout=20)
    rec("E6b", "errors", "OBS", "last request without trailing newline before EOF", {"rc": p.returncode,
                                                                                  "stdout": p.stdout.decode()})

    # EPIPE: reader goes away
    pr = subprocess.Popen([ENGINE, pizza], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    pr.stdout.close()
    t0 = time.time()
    try:
        for i in range(200):
            pr.stdin.write(b'{"id":%d,"op":"hello"}\n' % i)
            pr.stdin.flush()
    except BrokenPipeError:
        pass
    try:
        rc = pr.wait(timeout=10)
    except subprocess.TimeoutExpired:
        pr.kill()
        rc = "HANG"
    err = pr.stderr.read().decode(errors="replace")
    hard("E5", "errors", rc != "HANG", "stdout closed (EPIPE): engine terminates instead of hanging/spinning",
         {"rc": rc, "signal": (-rc if isinstance(rc, int) and rc < 0 else None), "stderr": err[:300],
          "secs": round(time.time() - t0, 2)})

    # long-lived process: many requests, no stack growth
    pr = subprocess.Popen([ENGINE, pizza], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    n_blank, n_req = 2_000_000, 200_000
    payload = b"\n" * n_blank + b'{"id":1,"op":"step","snapshot":{"at":"menu"},"event":"btn:order"}\n' * n_req
    t0 = time.time()
    out, err = pr.communicate(payload, timeout=600)
    ru = resource.getrusage(resource.RUSAGE_CHILDREN)
    hard("E8", "errors", pr.returncode == 0 and out.count(b"\n") == n_req,
         f"{n_blank} blank lines + {n_req} requests in one process (no stack growth / crash)",
         {"rc": pr.returncode, "replies": out.count(b"\n"), "secs": round(time.time() - t0, 1),
          "maxrss_kb_children": ru.ru_maxrss, "stderr": err[:200].decode(errors="replace")})


def big_line_scenarios(ex, own):
    pizza = os.path.join(ex, "pizza.bot.json")

    def one(label, line, timeout=120):
        t0 = time.time()
        pr = subprocess.Popen(["prlimit", "--as=6000000000", "--", ENGINE, pizza], stdin=subprocess.PIPE,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            out, err = pr.communicate(line, timeout=timeout)
            rc = pr.returncode
        except subprocess.TimeoutExpired:
            pr.kill()
            out, err = pr.communicate()
            rc = "TIMEOUT"
        _, status, ru = os.wait4(-1, os.WNOHANG) if False else (0, 0, resource.getrusage(resource.RUSAGE_CHILDREN))
        return {"case": label, "bytes": len(line), "rc": rc, "secs": round(time.time() - t0, 2),
                "reply": out[:160].decode(errors="replace"), "stderr": err[:160].decode(errors="replace")}
    res = []
    MB = 1024 * 1024
    res.append(one("10MB event name", b'{"id":1,"op":"step","snapshot":{"at":"menu"},"event":"btn:' + b"a" * (10 * MB) + b'"}\n'))
    res.append(one("10MB id string", b'{"id":"' + b"a" * (10 * MB) + b'","op":"hello"}\n'))
    for n in (10_000, 20_000, 40_000):
        g = b",".join(b'"g%d":true' % i for i in range(n))
        res.append(one(f"guards object {n} keys", b'{"id":1,"op":"step","snapshot":{"at":"menu"},"event":"btn:order","guards":{' + g + b'}}\n'))
    for n in (50_000, 100_000, 200_000):
        res.append(one(f"token with {n} digits", b'{"id":1,"op":"step","snapshot":{"at":"menu"},"event":"timer:x","token":1' + b"0" * n + b'}\n'))
    rec("E4", "errors", "OBS", "very long request lines (fresh process each, AS limit 6 GB)", res)


def mutate_scenarios(ex, own):
    pizza = os.path.join(ex, "pizza.bot.json")
    a = E(pizza)
    b = E(pizza, mutate="drop-call:create_order")
    ha, hb = a(op="hello"), b(op="hello")
    path = [("btn:order", None), ("btn:cart", None), ("btn:checkout", {"cart_not_empty": True}),
            ("msg:text", {"in_delivery_zone": True}), ("payment:success", None)]
    la, lb = run_path(a, path)[-1], run_path(b, path)[-1]
    a.close(), b.close()
    rec("M1", "mutate", "OBS", "production binary accepts --mutate: a mutated engine is indistinguishable via "
        "hello and silently drops create_order", {"hello_normal": ha, "hello_mutant": hb,
                                                 "last_actions_normal": la["actions"], "last_actions_mutant": lb["actions"]})
    p = subprocess.run([ENGINE, "--mutate", "redirect:999", pizza], input=b'{"id":1,"op":"hello"}\n',
                       capture_output=True, timeout=20)
    q = subprocess.run([ENGINE, "--mutate", "bogus", pizza], input=b"", capture_output=True, timeout=20)
    rec("M2", "mutate", "OBS", "out-of-range mutant index is accepted silently (no-op); unknown spec -> exit 2",
        {"redirect:999": [p.returncode, p.stdout.decode()], "bogus": [q.returncode, q.stderr.decode()[:120]]})


def run(engine, examples_dir):
    global ENGINE, TMP
    ENGINE = os.path.abspath(engine)
    TMP = os.environ.get("PT_DOCS_OUT") or tempfile.mkdtemp(prefix="ptsuite-")
    os.makedirs(TMP, exist_ok=True)
    own = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs")
    only = os.environ.get("PT_ONLY", "need,timers,seq,upgrade,errors,big,mutate").split(",")
    table = {"need": need_scenarios, "timers": timer_scenarios, "seq": seq_scenarios,
             "upgrade": upgrade_scenarios, "errors": error_scenarios, "big": big_line_scenarios,
             "mutate": mutate_scenarios}
    for k in only:
        table[k](examples_dir, own)
    fails = [r["id"] for r in RESULTS if r["verdict"] == "FAIL"]
    print(json.dumps({"scenarios": len(RESULTS), "pass": sum(r["verdict"] == "PASS" for r in RESULTS),
                      "fail": fails, "obs": sum(r["verdict"] == "OBS" for r in RESULTS), "docs_dir": TMP}))
    return 1 if fails or not RESULTS else 0

"""Own random generators (independent of Figram/RandomDoc.lean and Trace.lean):
documents, document upgrades (v1 -> v2), event sequences, stale snapshots."""

import copy
import json
import random

from oracle import MAX_INT, Bot, DocError

EVENTS = ["btn:a", "btn:b", "btn:c", "btn:d", "msg:text", "cmd:start"]
CONDS = ["c1", "c2", "c3", "c4"]
CALLS = ["k1", "k2", "k3"]
TIMER_POOL = ["t0", "t1", "t2", "t3", "t4", "t5"]


# ---------------------------------------------------------------- model
# model = {"nodes": {name: {"parent": p, "sec": bool, "initial": str|None,
#                           "on": {ev: [variant]}, "timers": {t: ms}, "ann": {}}},
#          "ann": {...}}   root name is ""

def children(model, p):
    return [n for n, x in model["nodes"].items() if x["parent"] == p and n != ""]


def to_json(model, rng=None, shuffle=False):
    nodes = model["nodes"]

    def node(n):
        x = nodes[n]
        o = {}
        if x["sec"]:
            o["initial"] = x["initial"]
        if x["on"]:
            o["on"] = {ev: [dict(v) for v in vs] for ev, vs in x["on"].items()}
        if x["sec"]:
            ks = children(model, n)
            if shuffle and rng:
                rng.shuffle(ks)
            o["children"] = {k: node(k) for k in ks}
        elif x["timers"]:
            o["timers"] = dict(x["timers"])
        for k, v in x.get("ann", {}).items():
            o[k] = v
        return o

    doc = {"figram": 1}
    if model.get("conditions"):
        doc["conditions"] = dict(model["conditions"])
    if model.get("calls"):
        doc["calls"] = dict(model["calls"])
    doc["bot"] = node("")
    for k, v in model.get("ann", {}).items():
        doc[k] = v
    return doc


def dumps(doc):
    return json.dumps(doc, ensure_ascii=False)


def gen_model(rng, n_msgs=8, n_secs=3, max_depth=4, p_hist=0.3, p_timer=0.35,
              p_ann=0.2, n_events=None, shared_timers=True):
    nodes = {"": {"parent": None, "sec": True, "initial": None, "on": {}, "timers": {}, "ann": {}}}
    depth = {"": 0}
    secs = [""]
    for i in range(n_secs):
        cand = [s for s in secs if depth[s] < max_depth]
        p = rng.choice(cand)
        name = f"s{i}"
        nodes[name] = {"parent": p, "sec": True, "initial": None, "on": {}, "timers": {}, "ann": {}}
        depth[name] = depth[p] + 1
        secs.append(name)
    msgs = []
    for i in range(n_msgs):
        # first make sure every section gets at least one message (eventually)
        empty = [s for s in secs if not children({"nodes": nodes}, s)]
        p = empty[-1] if empty else rng.choice(secs)
        name = f"m{i}"
        nodes[name] = {"parent": p, "sec": False, "initial": None, "on": {}, "timers": {}, "ann": {}}
        msgs.append(name)
    model = {"nodes": nodes, "conditions": {c: f"cond {c}" for c in CONDS},
             "calls": {c: f"call {c}" for c in CALLS}, "ann": {}}
    # sections with no children at all (all messages used) get one now
    k = n_msgs
    for s in secs:
        if not children(model, s):
            name = f"m{k}"
            k += 1
            nodes[name] = {"parent": s, "sec": False, "initial": None, "on": {}, "timers": {}, "ann": {}}
            msgs.append(name)
    for s in secs:
        nodes[s]["initial"] = rng.choice(children(model, s))
    # timers
    for m in msgs:
        if rng.random() < p_timer:
            pool = TIMER_POOL if shared_timers else [f"t_{m}_{j}" for j in range(2)]
            for t in rng.sample(pool, rng.choice([1, 1, 2])):
                nodes[m]["timers"][t] = rng.choice([0, 1, 1000, 60000, 7200000])
    all_timers = sorted({t for m in msgs for t in nodes[m]["timers"]})
    hist_secs = [s for s in secs if s != "" and rng.random() < p_hist * 2]
    allnodes = [n for n in nodes if n != ""]
    evpool = EVENTS[: n_events] if n_events else EVENTS

    def variants(owner):
        cnt = rng.choice([1, 1, 2, 2, 3])
        vs = []
        for j in range(cnt):
            v = {}
            last = j == cnt - 1
            if not last or rng.random() < 0.4:
                v["if"] = rng.choice(CONDS)
            r = rng.random()
            if hist_secs and r < p_hist:
                v["to"] = rng.choice(hist_secs)
                v["resume"] = True
            elif r < p_hist + 0.15 and not nodes[owner]["sec"]:
                v["to"] = owner  # self loop
            else:
                v["to"] = rng.choice(allnodes)
            if rng.random() < 0.4:
                v["do"] = [rng.choice(CALLS) for _ in range(rng.choice([1, 2, 3]))]
            if rng.random() < p_ann:
                v["x-ui"] = {"label": f"v{j}"}
            vs.append(v)
        return vs

    for n in [""] + allnodes:
        p_arrow = 0.35 if not nodes[n]["sec"] else 0.25
        for ev in evpool:
            if rng.random() < p_arrow:
                nodes[n]["on"][ev] = variants(n)
        # timer handlers: on owner, on sections (section-level), on non-owners
        for t in all_timers:
            owner = t in nodes[n]["timers"]
            p = 0.7 if owner else (0.25 if nodes[n]["sec"] else 0.08)
            if rng.random() < p:
                nodes[n]["on"]["timer:" + t] = variants(n)
        if rng.random() < p_ann:
            nodes[n]["ann"]["x-ui"] = {"x": rng.randint(0, 999), "y": rng.randint(0, 999)}
    if rng.random() < p_ann:
        model["ann"]["x-author"] = "audit"
    return model


def model_ok(model):
    try:
        Bot(dumps(to_json(model)))
        return True
    except DocError:
        return False


# ---------------------------------------------------------------- upgrades
def upgrade(rng, model, focus=None):
    """Return (v2 model, description). `focus` = names worth touching
    (current message, remembered places, ...)."""
    m = copy.deepcopy(model)
    nodes = m["nodes"]
    msgs = [n for n, x in nodes.items() if not x["sec"]]
    secs = [n for n, x in nodes.items() if x["sec"]]
    ops = ["delete_msg", "move_msg", "rename_timer", "move_timer", "add_hist", "drop_hist",
           "msg_to_sec", "drop_timer"]
    op = rng.choice(ops)
    pick = lambda pool: rng.choice([x for x in (focus or []) if x in pool] or pool)
    desc = op
    if op == "delete_msg":
        victim = pick(msgs)
        par = nodes[victim]["parent"]
        if len(children(m, par)) < 2:
            return None, None
        del nodes[victim]
        for x in nodes.values():
            if x["sec"] and x["initial"] == victim:
                x["initial"] = rng.choice(children(m, par))
            for ev in list(x["on"]):
                x["on"][ev] = [v for v in x["on"][ev] if v["to"] != victim]
                if not x["on"][ev]:
                    del x["on"][ev]
        desc = f"delete {victim}"
    elif op == "move_msg":
        victim = pick(msgs)
        par = nodes[victim]["parent"]
        if len(children(m, par)) < 2:
            return None, None
        new = rng.choice([s for s in secs if s != par])
        nodes[victim]["parent"] = new
        if nodes[par]["initial"] == victim:
            nodes[par]["initial"] = rng.choice(children(m, par))
        desc = f"move {victim} {par!r}->{new!r}"
    elif op in ("rename_timer", "move_timer", "drop_timer"):
        owners = [n for n in msgs if nodes[n]["timers"]]
        if not owners:
            return None, None
        o = pick(owners)
        t = rng.choice(list(nodes[o]["timers"]))
        ms = nodes[o]["timers"].pop(t)
        if op == "rename_timer":
            nt = t + "x"
            nodes[o]["timers"][nt] = ms
            if "timer:" + t in nodes[o]["on"]:
                nodes[o]["on"]["timer:" + nt] = nodes[o]["on"].pop("timer:" + t)
            desc = f"rename timer {t}->{nt} on {o}"
        elif op == "move_timer":
            dst = rng.choice([x for x in msgs if x != o])
            nodes[dst]["timers"][t] = ms
            desc = f"move timer {t} {o}->{dst}"
        else:
            desc = f"drop timer {t} from {o}"
        # keep V5: drop timer:<t> arrows if no timer t remains
        left = {tt for x in nodes.values() for tt in x["timers"]}
        for x in nodes.values():
            for ev in list(x["on"]):
                if ev.startswith("timer:") and ev[6:] not in left:
                    del x["on"][ev]
    elif op == "add_hist":
        s = rng.choice([x for x in secs if x != ""] or [None])
        if s is None:
            return None, None
        src = rng.choice([n for n in nodes if n != ""])
        nodes[src]["on"]["btn:hist"] = [{"to": s, "resume": True}]
        desc = f"add resume arrow {src}->{s}"
    elif op == "drop_hist":
        for x in nodes.values():
            for ev in list(x["on"]):
                x["on"][ev] = [v for v in x["on"][ev] if not v.get("resume")]
                if not x["on"][ev]:
                    del x["on"][ev]
        desc = "drop all resume arrows"
    elif op == "msg_to_sec":
        victim = pick(msgs)
        child = victim + "_in"
        nodes[child] = {"parent": victim, "sec": False, "initial": None, "on": {},
                        "timers": nodes[victim]["timers"], "ann": {}}
        nodes[victim]["sec"] = True
        nodes[victim]["timers"] = {}
        nodes[victim]["initial"] = child
        desc = f"message {victim} becomes a section"
    if not model_ok(m):
        return None, None
    return m, desc


# ---------------------------------------------------------------- events
def pick_event(rng, bot, snap, history_tokens):
    """Returns (event, token|None, kind)."""
    r = rng.random()
    timers = snap["timers"]
    doc_timers = sorted({t for n in bot.nodes.values() for t, _ in n.timers})
    if r < 0.15 and timers:
        t = rng.choice(list(timers))
        return "timer:" + t, timers[t], "timer-valid"
    if r < 0.20 and timers:
        t = rng.choice(list(timers))
        old = [x for x in history_tokens.get(t, []) if x != timers[t]]
        tok = rng.choice(old) if old else max(0, timers[t] - 1)
        return "timer:" + t, tok, "timer-stale"
    if r < 0.23:
        t = rng.choice(doc_timers + ["nope"]) if doc_timers else "nope"
        tok = rng.choice([0, 1, snap["seq"], snap["seq"] + 1, MAX_INT, 10 ** 18, rng.randint(0, 50)])
        return "timer:" + t, tok, "timer-forged"
    if r < 0.26:
        return rng.choice(["unknown:event", "btn:zzz"]), None, "unknown"
    at = snap["at"]
    if bot.is_message(at):
        evs = sorted({e for n in [at] + bot.enclosing(at) for e, _ in bot.nodes[n].on
                      if not e.startswith("timer:")})
        if evs:
            return rng.choice(evs), None, "live"
    return rng.choice(EVENTS), None, "named"


def guard_plan(rng, need):
    return {g: rng.random() < 0.5 for g in sorted(need)}


def perturb_snapshot(rng, bot, snap, older, doc_names):
    """Stale / forged snapshots the host might hand us."""
    s = {"at": snap["at"], "resume": dict(snap["resume"]), "timers": dict(snap["timers"]),
         "seq": snap["seq"]}
    secs = [n for n, x in bot.nodes.items() if x.is_section and n != ""]
    msgs = [n for n, x in bot.nodes.items() if not x.is_section]
    k = rng.choice(["older", "deleted_at", "section_at", "bad_resume", "foreign_resume",
                    "extra_timer", "seq_edge"])
    if k == "older" and older:
        o = rng.choice(older)
        s = {"at": o["at"], "resume": dict(o["resume"]), "timers": dict(o["timers"]), "seq": o["seq"]}
    elif k == "deleted_at":
        s["at"] = "gone_" + str(rng.randint(0, 9))
    elif k == "section_at" and secs:
        s["at"] = rng.choice(secs)
    elif k == "bad_resume" and secs:
        s["resume"][rng.choice(secs)] = rng.choice(["gone_msg"] + secs + msgs)
    elif k == "foreign_resume":
        s["resume"]["no_such_section"] = rng.choice(msgs)
    elif k == "extra_timer":
        t = rng.choice(TIMER_POOL + ["zz"])
        s["timers"][t] = rng.randint(0, s["seq"] + 2)
    elif k == "seq_edge":
        s["seq"] = MAX_INT - rng.choice([0, 1, 2])
    return s, k

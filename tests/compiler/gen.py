"""Own random generators (independent of Figram/RandomDoc.lean and Trace.lean):
documents, document upgrades (v1 -> v2), event sequences, stale snapshots."""

import copy
import json
import random

from oracle import MAX_INT, Bot, DocError, copy_snap

EVENTS = ["btn:a", "btn:b", "btn:c", "btn:d", "msg:text", "cmd:start"]
CONDS = ["c1", "c2", "c3", "c4"]
CALLS = ["k1", "k2", "k3"]
TIMER_POOL = ["t0", "t1", "t2", "t3", "t4", "t5"]
# Actions with properties (docs/format.md, section 4 `Call`, decisions 11-12 of section 7):
# background, with outcomes (latest / every), a name with `:` (the event is split at its last `:`).
FEATURE_CALLS = {
    "d1": {"text": "call d1 (background)", "detached": True},
    "o1": {"text": "call o1", "outcomes": ["ok", "no"]},
    "o2": {"text": "call o2 (every call answered)", "outcomes": ["done"], "every": True},
    "o3:x": {"text": "call o3:x (background, latest)", "outcomes": ["s"], "detached": True},
    "o4": {"text": "call o4", "outcomes": ["a", "b"], "every": True, "detached": True},
}


def has_outcomes(decl):
    return isinstance(decl, dict) and bool(decl.get("outcomes"))


def outcome_map(model):
    """{action: its outcomes + failed} for the actions with outcomes of a model."""
    return {k: list(v["outcomes"]) + ["failed"] for k, v in model.get("calls", {}).items() if has_outcomes(v)}


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


def gen_calls(rng, p_feat):
    """The `calls` map: the plain actions, and with probability p_feat each action with
    properties; sometimes written in the object form or with explicit defaults
    (decision 11 of section 3: a reader accepts both)."""
    calls = {}
    for c in CALLS:
        r = rng.random()
        if r < 0.1:
            calls[c] = {"text": f"call {c}"}
        elif r < 0.15:
            calls[c] = {"text": f"call {c}", "detached": False, "every": False}
        elif r < 0.21:
            calls[c] = {"text": f"call {c}", "outcomes": []}   # A13: read as no outcomes
        elif r < 0.23:
            calls[c] = {"text": f"call {c}", "x-ui": {"color": "red"}}
        else:
            calls[c] = f"call {c}"
    for c, decl in FEATURE_CALLS.items():
        if rng.random() < p_feat:
            calls[c] = copy.deepcopy(decl)
    return calls


def gen_model(rng, n_msgs=8, n_secs=3, max_depth=4, p_hist=0.3, p_timer=0.35,
              p_ann=0.2, n_events=None, shared_timers=True, p_back=0.15, p_feat=0.0, p_stay=0.0):
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
             "calls": {c: f"call {c}" for c in CALLS} if not p_feat else gen_calls(rng, p_feat), "ann": {}}
    call_names = list(model["calls"])
    outcomes = outcome_map(model)
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

    def do_list():
        do = [rng.choice(call_names) for _ in range(rng.choice([1, 2, 3]))]
        out, seen = [], set()
        for c in do:                 # V6: an action with outcomes at most once per `do`
            if c in outcomes and c in seen:
                continue
            seen.add(c)
            out.append(c)
        return out

    def variants(owner, p_stay_here=p_stay):
        cnt = rng.choice([1, 1, 2, 2, 3])
        vs = []
        for j in range(cnt):
            v = {}
            last = j == cnt - 1
            if not last or rng.random() < 0.4:
                v["if"] = rng.choice(CONDS)
            r = rng.random()
            if rng.random() < p_stay_here:
                pass                 # an arrow without a target (decision 13): no resume / back (V3)
            elif hist_secs and r < p_hist:
                v["to"] = rng.choice(hist_secs)
                v["resume"] = True
            elif r < p_hist + 0.15 and not nodes[owner]["sec"]:
                v["to"] = owner  # self loop
            else:
                v["to"] = rng.choice(allnodes)
            if "to" in v and rng.random() < p_back:
                v["back"] = True
            if rng.random() < (0.55 if outcomes else 0.4):
                v["do"] = do_list()
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
                nodes[n]["on"]["timer:" + t] = variants(n, min(1.0, p_stay * 2))
        # outcome arrows (V5: only declared outcomes or `failed`): on the root they handle the
        # outcome anywhere, on a section inside it, on a message only there (decision 12)
        for a, outs in outcomes.items():
            for o in outs:
                p = 0.35 if n == "" else (0.2 if nodes[n]["sec"] else 0.12)
                if rng.random() < p:
                    nodes[n]["on"][f"call:{a}:{o}"] = variants(n, min(1.0, p_stay * 2))
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
           "msg_to_sec", "drop_timer", "add_back"]
    if outcome_map(m):
        # waits of an action that is removed or loses its outcomes are kept and still accept
        # their outcome (docs/format.md, section 8; host-contract.md, section 6)
        ops += ["drop_outcomes", "remove_action", "toggle_every", "drop_outcomes", "remove_action"]
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
                x["on"][ev] = [v for v in x["on"][ev] if v.get("to") != victim]
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
    elif op == "add_back":
        src = rng.choice([n for n in nodes if n != ""])
        nodes[src]["on"]["btn:back"] = [{"to": rng.choice([n for n in nodes if n != ""]), "back": True}]
        desc = f"add back arrow on {src}"
    elif op == "drop_hist":
        for x in nodes.values():
            for ev in list(x["on"]):
                x["on"][ev] = [v for v in x["on"][ev] if not v.get("resume")]
                if not x["on"][ev]:
                    del x["on"][ev]
        desc = "drop all resume arrows"
    elif op in ("drop_outcomes", "remove_action", "toggle_every"):
        a = rng.choice(sorted(outcome_map(m)))
        if op == "toggle_every":
            d = m["calls"][a]
            if d.get("every"):
                del d["every"]
            else:
                d["every"] = True
            desc = f"toggle every of {a}"
        else:
            if op == "drop_outcomes":
                m["calls"][a] = m["calls"][a]["text"]
                desc = f"drop outcomes of {a}"
            else:
                del m["calls"][a]
                for x in nodes.values():
                    for vs in x["on"].values():
                        for v in vs:
                            if "do" in v:
                                v["do"] = [c for c in v["do"] if c != a]
                                if not v["do"]:
                                    del v["do"]
                desc = f"remove action {a}"
            for x in nodes.values():         # keep V5
                for ev in list(x["on"]):
                    if ev.startswith(f"call:{a}:") and ":" not in ev[len(f"call:{a}:"):]:
                        del x["on"][ev]
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
    # outcome events (docs/format.md, section 7, decision 12): with the token of a wait of
    # the snapshot (accepted), with a wrong token (ignored), an undeclared outcome name, an
    # outcome of an action without a wait
    pend = snap.get("pending", [])
    outs = bot.outcome_actions()
    if pend and (r < 0.42 or rng.random() < 0.2):
        w = rng.choice(pend)
        o = rng.choice(outs.get(w["call"], ["ok", "failed"]))
        q = rng.random()
        if q < 0.72:
            return f"call:{w['call']}:{o}", w["token"], "outcome-valid"
        if q < 0.86:
            old = [t for t in history_tokens.get("call:" + w["call"], []) if t != w["token"]]
            tok = rng.choice(old + [max(0, w["token"] - 1), w["token"] + 1, snap["seq"]])
            if any(x["call"] == w["call"] and x["token"] == tok for x in pend):
                tok = MAX_INT
            return f"call:{w['call']}:{o}", tok, "outcome-stale"
        if q < 0.93:
            return f"call:{w['call']}:bogus", w["token"], "outcome-undeclared"
        other = [a for a in outs if a != w["call"]] + ["k1", "nope"]
        return f"call:{rng.choice(other)}:{o}", w["token"], "outcome-foreign"
    if r < 0.45 and outs:
        a = rng.choice(sorted(outs))
        tok = rng.choice([0, 1, snap["seq"], snap["seq"] + 1, rng.randint(0, 50)])
        return f"call:{a}:{rng.choice(outs[a])}", tok, "outcome-forged"
    at = snap["at"]
    if bot.is_message(at):
        evs = sorted({e for n in [at] + bot.enclosing(at) for e, _ in bot.nodes[n].on
                      if not e.startswith("timer:") and not e.startswith("call:")})
        if evs:
            return rng.choice(evs), None, "live"
    return rng.choice(EVENTS), None, "named"


def guard_plan(rng, need):
    return {g: rng.random() < 0.5 for g in sorted(need)}


def perturb_snapshot(rng, bot, snap, older, doc_names):
    """Stale / forged snapshots the host might hand us."""
    s = copy_snap(snap)
    secs = [n for n, x in bot.nodes.items() if x.is_section and n != ""]
    msgs = [n for n, x in bot.nodes.items() if not x.is_section]
    kinds = ["older", "deleted_at", "section_at", "bad_resume", "foreign_resume",
             "extra_timer", "seq_edge", "bad_back", "other_back"]
    if bot.outcome_actions() or s["pending"]:
        kinds += ["forged_wait", "foreign_wait", "dup_wait", "drop_waits", "multi_wait"]
    k = rng.choice(kinds)
    if k == "older" and older:
        s = copy_snap(rng.choice(older))
    elif k == "forged_wait":
        a = rng.choice(sorted(bot.outcome_actions()) or ["o1"])
        s["pending"].insert(rng.randint(0, len(s["pending"])), {"call": a, "token": rng.randint(0, s["seq"] + 1)})
    elif k == "foreign_wait":
        # a wait of an action that is not in the document, or has no outcomes (section 8)
        s["pending"].insert(0, {"call": rng.choice(["k1", "gone_action", "d1"]), "token": rng.randint(0, s["seq"])})
    elif k == "dup_wait" and s["pending"]:
        w = rng.choice(s["pending"])
        s["pending"].insert(rng.randint(0, len(s["pending"])), dict(w))
    elif k == "drop_waits":
        s["pending"] = []
    elif k == "multi_wait":
        # several waits of one action (written while it had `every`, or forged)
        a = rng.choice([w["call"] for w in s["pending"]] or sorted(bot.outcome_actions()))
        for _ in range(rng.randint(1, 2)):
            s["pending"].insert(rng.randint(0, len(s["pending"])), {"call": a, "token": rng.randint(0, s["seq"])})
    elif k == "bad_back":
        s["back"] = rng.choice(["gone_msg"] + secs) if secs else "gone_msg"
    elif k == "other_back":
        s["back"] = rng.choice(msgs + [None])
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

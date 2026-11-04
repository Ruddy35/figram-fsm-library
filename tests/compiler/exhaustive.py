"""Branch-exhaustive cases: for EVERY message L, every event that has
candidates at L (bubbling chain), and every candidate index i, build a
snapshot at L and guard values that make variant i the winner (plus the
"all false" case).  Variants that cannot be selected by any guard values
(after an unconditional variant, or repeating a guard that had to be false)
are counted as dead."""

import random

from oracle import snap_to_json, split_outcome


def select_guards(cands, i):
    """Guard values making cands[i] the first true variant, or None if dead."""
    g = {}
    for v in cands[:i]:
        if v.cond is None:
            return None
        g[v.cond] = False
    vi = cands[i]
    if vi.cond is not None:
        if g.get(vi.cond) is False:
            return None
        g[vi.cond] = True
    return g


def all_false(cands):
    g = {}
    for v in cands:
        if v.cond is None:
            return None
        g[v.cond] = False
    return g


def snapshot_at(bot, L, rng, seq, memory="random"):
    resume = {}
    for s in sorted(bot.history):
        inside = [m for m in bot.nodes if bot.is_message(m) and s in bot.enclosing(m)]
        if memory == "absent":
            continue
        if memory == "member":
            other = [m for m in inside if m != bot.landing(s)]
            resume[s] = rng.choice(other or inside)
            continue
        if memory == "distinct":
            used = set(resume.values())
            other = [m for m in inside if m not in used and m != bot.landing(s)]
            resume[s] = rng.choice(other or inside)
            continue
        r = rng.random()
        if r < 0.5:
            resume[s] = rng.choice(inside)
        elif r < 0.6:
            resume[s] = "gone_msg"
    # as if just entered L: its timers carry the current token
    timers = {t: seq for t, _ in bot.nodes[L].timers}
    msgs = [m for m in bot.nodes if bot.is_message(m)]
    r = rng.random()
    back = rng.choice(msgs) if r < 0.6 else ("gone_msg" if r < 0.7 else None)
    # waits of the actions with outcomes (decision 12), sometimes none
    pending = []
    for a in sorted(bot.outcome_actions()):
        if rng.random() < 0.3:
            pending.append({"call": a, "token": rng.randint(0, seq)})
    return {"at": L, "resume": resume, "timers": timers, "seq": seq, "back": back, "pending": pending}


def cases(bot, rng):
    """Yields (snapshot_json, event, token, guards_or_None, tag)."""
    msgs = [m for m in bot.nodes if bot.is_message(m)]
    for L in msgs:
        evs = []
        for owner in [L] + bot.enclosing(L):
            for e, _ in bot.nodes[owner].on:
                if e not in evs:
                    evs.append(e)
        for e in evs:
            cands = bot.candidates(L, e)
            seq = rng.randint(1, 50)
            for i, tail, memory in [(i, t, m) for i in range(len(cands) + 1)
                                    for t, m in ((False, "absent"), (True, "member"), (None, "random"),
                                                 (False, "distinct"), (True, "absent"))]:
                g = select_guards(cands, i) if i < len(cands) else all_false(cands)
                if g is None:
                    if i < len(cands) and tail is False:
                        yield None, e, None, None, ("dead", L, e, i)
                    continue
                for v in cands[i + 1:]:
                    if v.cond is not None and v.cond not in g:
                        g[v.cond] = tail if tail is not None else rng.random() < 0.5
                s = snapshot_at(bot, L, rng, seq, memory)
                tok = None
                tag = "branch" if i < len(cands) else "all-false"
                if e.startswith("timer:"):
                    t = e[6:]
                    if t not in s["timers"]:
                        s["timers"][t] = seq      # stale/forged entry (not owned by L)
                        tag += "-unowned-timer"
                    tok = s["timers"][t]
                if e.startswith("call:"):
                    # an outcome: accepted only with the token of a wait (decision 12)
                    a = split_outcome(e)[0]
                    waits = [w for w in s["pending"] if w["call"] == a]
                    if not waits:
                        s["pending"].insert(rng.randint(0, len(s["pending"])), {"call": a, "token": seq})
                        waits = [{"call": a, "token": seq}]
                    tok = rng.choice(waits)["token"]
                    if rng.random() < 0.1:
                        tok = seq + 1                # no such wait: ignored
                        tag += "-stale-outcome"
                yield snap_to_json(s), e, tok, g, (tag, L, e, i)


def initial_cases(bot, rng):
    """Branch-exhaustive cases at the INITIAL message for a user without a message:
    `start` with an event (decision 9) and recovery (decision 8: `at` names no message, a
    named event or an ACCEPTED outcome is matched as at the initial message, decision 12;
    a variant without a target leads to the initial message, decision 13).
    Yields ("start", event, None, guards, seq, tag) and ("step", event, token, guards,
    snapshot_json, tag)."""
    init = bot.landing(bot.ROOT)
    evs = []
    for owner in [init] + bot.enclosing(init):
        for e, _ in bot.nodes[owner].on:
            if e not in evs and not e.startswith("timer:"):
                evs.append(e)
    msgs = [m for m in bot.nodes if bot.is_message(m)]
    secs = [n for n, x in bot.nodes.items() if x.is_section and n != bot.ROOT]
    for e in evs:
        cands = bot.candidates(init, e)
        for i in range(len(cands) + 1):
            g = select_guards(cands, i) if i < len(cands) else all_false(cands)
            if g is None:
                continue
            for v in cands[i + 1:]:
                if v.cond is not None and v.cond not in g:
                    g[v.cond] = rng.random() < 0.5
            tag = ("init-branch" if i < len(cands) else "init-all-false",
                   "stay" if i < len(cands) and cands[i].to is None else "move")
            if not e.startswith("call:"):
                yield "start", e, None, g, rng.choice([0, 0, 7]), ("start",) + tag
            seq = rng.randint(1, 40)
            s = snapshot_at(bot, msgs[0], rng, seq)
            s["at"] = rng.choice(["gone_msg"] + secs)
            s["timers"] = {t: rng.randint(0, seq) for t in rng.sample(["t0", "t1", "zz"], rng.randint(0, 2))}
            tok = None
            if e.startswith("call:"):
                a = split_outcome(e)[0]
                w = {"call": a, "token": rng.randint(0, seq)}
                s["pending"] = [x for x in s["pending"] if x != w]
                if rng.random() < 0.8:       # accepted; otherwise no wait: the plain start over
                    s["pending"].insert(rng.randint(0, len(s["pending"])), w)
                    tag = tag + ("accepted",)
                else:
                    tag = tag + ("not-accepted",)
                tok = w["token"]
            yield "step", e, tok, g, snap_to_json(s), ("recover",) + tag

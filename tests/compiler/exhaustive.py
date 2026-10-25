"""Branch-exhaustive cases: for EVERY message L, every event that has
candidates at L (bubbling chain), and every candidate index i, build a
snapshot at L and guard values that make variant i the winner (plus the
"all false" case).  Variants that cannot be selected by any guard values
(after an unconditional variant, or repeating a guard that had to be false)
are counted as dead."""

import random

from oracle import snap_to_json


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
    return {"at": L, "resume": resume, "timers": timers, "seq": seq, "back": back}


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
                yield snap_to_json(s), e, tok, g, (tag, L, e, i)

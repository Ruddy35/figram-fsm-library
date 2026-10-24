"""Lock-step comparison of one step: oracle (oracle.py) vs real engine."""

import random

from oracle import Bot, RequestError, copy_snap, snap_from_json, snap_to_json


class Divergence(Exception):
    def __init__(self, what, **ctx):
        super().__init__(what)
        self.what = what
        self.ctx = ctx


def norm_snap(j):
    s = snap_from_json(j)
    return {"at": s["at"], "resume": dict(s["resume"]), "timers": dict(s["timers"]), "seq": s["seq"]}


def compare_result(bot, snap_in, rep, ev, token, guards, hits, stats):
    """Compare an engine result/error reply with the oracle evaluated with the
    guards the engine actually received."""
    try:
        o = bot.step(snap_in, ev, token, guards, hits=hits, check_need=False)
    except RequestError as e:
        if rep.get("error") != str(e):
            raise Divergence("oracle error, engine did not", oracle=str(e), engine=rep)
        stats["errors"] = stats.get("errors", 0) + 1
        return None
    if "error" in rep:
        raise Divergence("engine error, oracle did not", engine=rep, oracle=o)
    _, s2, acts, rec = o
    es = norm_snap(rep["snapshot"])
    if es != s2:
        raise Divergence("snapshot differs", engine=es, oracle=s2)
    if rep["actions"] != acts:
        raise Divergence("actions differ", engine=rep["actions"], oracle=acts)
    if bool(rep.get("recovered", False)) != rec:
        raise Divergence("recovered flag differs", engine=rep.get("recovered"), oracle=rec)
    if "recovered" in rep and rep["recovered"] is not True:
        raise Divergence("recovered must be literally true", engine=rep)
    if rec:
        stats["recovered"] = stats.get("recovered", 0) + 1
    if acts:
        stats["transitions"] = stats.get("transitions", 0) + 1
    else:
        stats["ignored"] = stats.get("ignored", 0) + 1
    return rep["snapshot"]


def do_step(bot, eng, snap_json, ev, token, rng, hits, stats, guard_mode=None, fixed_guards=None):
    try:
        return _do_step(bot, eng, snap_json, ev, token, rng, hits, stats, guard_mode, fixed_guards)
    except Divergence as d:
        d.ctx.setdefault("input", {"snapshot": snap_json, "event": ev, "token": token,
                                   "guards": d.ctx.get("given", fixed_guards)})
        raise


def _do_step(bot, eng, snap_json, ev, token, rng, hits, stats, guard_mode=None, fixed_guards=None):
    """Run one protocol step on the engine (answering `need` like a host) and
    compare everything with the oracle.  Returns the engine's new snapshot JSON
    (or the old one if the step errored) and the trace record."""
    snap = snap_from_json(snap_json)
    guard_mode = guard_mode or rng.choice(["lazy", "lazy", "full", "partial"])
    mn_all, al_all = bot.need_sets(snap, ev, token, {})
    given = {}
    if fixed_guards is not None:
        given = dict(fixed_guards)
    elif guard_mode == "full":
        given = {g: rng.random() < 0.5 for g in sorted(al_all)}
    elif guard_mode == "partial":
        given = {g: rng.random() < 0.5 for g in sorted(al_all) if rng.random() < 0.5}
    if fixed_guards is None and rng.random() < 0.1:
        given["irrelevant_guard"] = True
    req = {"op": "step", "snapshot": snap_json, "event": ev}
    if token is not None:
        req["token"] = token
    rounds = 0
    record = {"snapshot": snap_json, "event": ev, "token": token}
    while True:
        rounds += 1
        if given:
            req["guards"] = dict(given)
        rep = eng.req(req)
        stats["requests"] = stats.get("requests", 0) + 1
        if rep is None:
            raise Divergence("engine died", request=req)
        mn, al = bot.need_sets(snap, ev, token, given)
        if "need" in rep:
            stats["need_replies"] = stats.get("need_replies", 0) + 1
            n = rep["need"]
            ns = set(n)
            if len(ns) != len(n):
                raise Divergence("need has duplicates", engine=n)
            if not ns:
                raise Divergence("empty need", engine=n)
            if not (mn <= ns <= al):
                raise Divergence("need outside [minimal, all]", engine=sorted(ns),
                                 minimal=sorted(mn), all=sorted(al), given=given)
            if ns != mn:
                stats["need_superfluous"] = stats.get("need_superfluous", 0) + 1
                hits.add("A3-need-superset")
            if rounds > 1:
                raise Divergence("second need after all requested guards were sent", engine=n)
            for g in sorted(ns):
                given[g] = rng.random() < 0.5
            continue
        if mn:
            raise Divergence("engine stepped without a needed guard", minimal=sorted(mn), engine=rep)
        record["guards"] = dict(given)
        try:
            new = compare_result(bot, snap, rep, ev, token, given, hits, stats)
        except Divergence as d:
            d.ctx["input"] = {"snapshot": snap_json, "event": ev, "token": token, "guards": dict(given)}
            raise
        record["reply"] = rep
        return (new if new is not None else snap_json), record


def do_start(bot, eng, stats):
    rep = eng.req({"op": "start"})
    stats["requests"] = stats.get("requests", 0) + 1
    s, acts = bot.start()
    if norm_snap(rep["snapshot"]) != s or rep["actions"] != acts or "recovered" in rep:
        raise Divergence("start differs", engine=rep, oracle=(s, acts))
    return rep["snapshot"]


def do_start_with(bot, eng, ev, rng, stats, seq=0):
    """`start` with a first event on the engine (answering `need` like a host), compared with
    the oracle evaluated with the guards the engine actually received."""
    given = {}
    if rng.random() < 0.3:
        _, al = bot.need_sets({"at": bot.landing(bot.ROOT), "resume": {}, "timers": {}, "seq": seq}, ev, None, {})
        given = {g: rng.random() < 0.5 for g in sorted(al)}
    req = {"op": "start", "event": ev}
    if seq:
        req["seq"] = seq
    for rounds in (1, 2):
        if given:
            req["guards"] = dict(given)
        rep = eng.req(req)
        stats["requests"] = stats.get("requests", 0) + 1
        if rep is None:
            raise Divergence("engine died", request=req)
        here = {"at": bot.landing(bot.ROOT), "resume": {}, "timers": {}, "seq": seq}
        mn, al = bot.need_sets(here, ev, None, given)
        if "need" in rep:
            ns = set(rep["need"])
            if not ns or len(ns) != len(rep["need"]) or not (mn <= ns <= al) or rounds > 1:
                raise Divergence("start with event: need outside [minimal, all]", engine=rep,
                                 minimal=sorted(mn), all=sorted(al), request=req)
            stats["need_replies"] = stats.get("need_replies", 0) + 1
            for g in rep["need"]:
                given[g] = rng.random() < 0.5
            continue
        if mn:
            raise Divergence("start with event: engine did not ask for a needed guard", engine=rep,
                             minimal=sorted(mn), request=req)
        _, s, acts = bot.start_with(ev, given, seq, check_need=False)
        if "error" in rep or norm_snap(rep["snapshot"]) != s or rep["actions"] != acts or "recovered" in rep:
            raise Divergence("start with event differs", engine=rep, oracle=(s, acts), request=req)
        stats["start_events"] = stats.get("start_events", 0) + 1
        return rep["snapshot"]


def do_restart(bot, eng, snap_json, stats):
    rep = eng.req({"op": "restart", "snapshot": snap_json})
    stats["requests"] = stats.get("requests", 0) + 1
    try:
        s, acts = bot.restart(snap_from_json(snap_json))
    except RequestError as e:
        if rep.get("error") != str(e):
            raise Divergence("restart: oracle error only", engine=rep, oracle=str(e))
        return snap_json
    if "error" in rep or norm_snap(rep["snapshot"]) != s or rep["actions"] != acts or "recovered" in rep:
        raise Divergence("restart differs", engine=rep, oracle=(s, acts))
    stats["restarts"] = stats.get("restarts", 0) + 1
    return rep["snapshot"]

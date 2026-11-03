"""Lock-step comparison of one step: oracle (oracle.py) vs real engine."""

import json
import random

from oracle import Bot, RequestError, copy_snap, snap_from_json, snap_to_json


class Divergence(Exception):
    def __init__(self, what, **ctx):
        super().__init__(what)
        self.what = what
        self.ctx = ctx


def norm_snap(j):
    s = snap_from_json(j)
    return {"at": s["at"], "resume": dict(s["resume"]), "timers": dict(s["timers"]), "seq": s["seq"],
            "back": s["back"], "pending": [dict(w) for w in s["pending"]]}


def expected_line(eid, snap, acts, rec, raw, stats=None):
    """The reply line the oracle expects, byte for byte (compact JSON, the field order of
    docs/format.md, section 8, defaults omitted: no empty `pending`, no `"detached": false`,
    `token` only on a call with outcomes, `recovered` only when true).  The order of the
    keys INSIDE `resume` and `timers` is not specified by the prose: when the key sets agree
    the engine's order is taken (and counted)."""
    exp = {"id": eid, "snapshot": snap_to_json(snap), "actions": acts}
    if rec:
        exp["recovered"] = True
    try:
        er = json.loads(raw).get("snapshot", {})
    except ValueError:
        er = {}
    for k in ("resume", "timers"):
        em, om = er.get(k), exp["snapshot"].get(k)
        if isinstance(em, dict) and isinstance(om, dict) and set(em) == set(om):
            if list(em) != list(om) and stats is not None:
                stats["map_order_taken_from_engine"] = stats.get("map_order_taken_from_engine", 0) + 1
            exp["snapshot"][k] = {x: om[x] for x in em}
    return json.dumps(exp, separators=(",", ":"), ensure_ascii=False)


def check_bytes(eng, snap, acts, rec, stats, what="reply bytes differ"):
    raw = getattr(eng, "last_line", None)
    if raw is None:
        return
    raw = raw.rstrip("\n")
    exp = expected_line(eng.n, snap, acts, rec, raw, stats)
    if raw != exp:
        raise Divergence(what, engine=raw, oracle=exp)
    stats["byte_compared"] = stats.get("byte_compared", 0) + 1


def count_features(acts, snap_in, s2, stats):
    """Coverage of the new protocol fields (detached / token on calls, waits)."""
    for a in acts:
        if "call" in a:
            for k in ("detached", "token"):
                if k in a:
                    stats["call_" + k] = stats.get("call_" + k, 0) + 1
    if s2.get("pending"):
        stats["replies_with_pending"] = stats.get("replies_with_pending", 0) + 1


def compare_result(bot, snap_in, rep, ev, token, guards, hits, stats, eng=None):
    """Compare an engine result/error reply with the oracle evaluated with the
    guards the engine actually received."""
    try:
        o = bot.step(snap_in, ev, token, guards, hits=hits, check_need=False)
    except RequestError as e:
        if rep.get("code") != e.code:
            raise Divergence("oracle error, engine did not", oracle=[e.code, e.text], engine=rep)
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
    if eng is not None:
        check_bytes(eng, s2, acts, rec, stats)
    count_features(acts, snap_in, s2, stats)
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
            new = compare_result(bot, snap, rep, ev, token, given, hits, stats, eng)
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
    check_bytes(eng, s, acts, False, stats, "start: reply bytes differ")
    return rep["snapshot"]


def do_start_with(bot, eng, ev, rng, stats, seq=0, hits=None, fixed_guards=None):
    """`start` with a first event on the engine (answering `need` like a host), compared with
    the oracle evaluated with the guards the engine actually received."""
    given = {}
    if fixed_guards is not None:
        given = dict(fixed_guards)
    elif rng.random() < 0.3:
        _, al = bot.need_sets({"at": bot.landing(bot.ROOT), "resume": {}, "timers": {}, "seq": seq,
                               "back": None, "pending": []}, ev, None, {})
        given = {g: rng.random() < 0.5 for g in sorted(al)}
    req = {"op": "start", "event": ev}
    if seq:
        req["seq"] = seq
    try:
        bot.classify(ev, None, for_start=True)
    except RequestError as e:      # a timer or an outcome event cannot start a user
        rep = eng.req(req)
        stats["requests"] = stats.get("requests", 0) + 1
        if rep is None or rep.get("code") != e.code or "snapshot" in rep:
            raise Divergence("start with event: oracle error only", engine=rep, oracle=[e.code, e.text],
                             request=req)
        stats["start_rejected"] = stats.get("start_rejected", 0) + 1
        return None
    for rounds in (1, 2):
        if given:
            req["guards"] = dict(given)
        rep = eng.req(req)
        stats["requests"] = stats.get("requests", 0) + 1
        if rep is None:
            raise Divergence("engine died", request=req)
        here = {"at": bot.landing(bot.ROOT), "resume": {}, "timers": {}, "seq": seq, "back": None,
                "pending": []}
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
        _, s, acts = bot.start_with(ev, given, seq, check_need=False, hits=hits)
        if "error" in rep or norm_snap(rep["snapshot"]) != s or rep["actions"] != acts or "recovered" in rep:
            raise Divergence("start with event differs", engine=rep, oracle=(s, acts), request=req)
        check_bytes(eng, s, acts, False, stats, "start with event: reply bytes differ")
        count_features(acts, None, s, stats)
        stats["start_events"] = stats.get("start_events", 0) + 1
        return rep["snapshot"]


def do_restart(bot, eng, snap_json, stats):
    rep = eng.req({"op": "restart", "snapshot": snap_json})
    stats["requests"] = stats.get("requests", 0) + 1
    try:
        s, acts = bot.restart(snap_from_json(snap_json))
    except RequestError as e:
        if rep.get("code") != e.code:
            raise Divergence("restart: oracle error only", engine=rep, oracle=[e.code, e.text])
        return snap_json
    if "error" in rep or norm_snap(rep["snapshot"]) != s or rep["actions"] != acts or "recovered" in rep:
        raise Divergence("restart differs", engine=rep, oracle=(s, acts))
    check_bytes(eng, s, acts, False, stats, "restart: reply bytes differ")
    if snap_from_json(snap_json)["pending"]:
        stats["restarts_with_pending"] = stats.get("restarts_with_pending", 0) + 1
    stats["restarts"] = stats.get("restarts", 0) + 1
    return rep["snapshot"]

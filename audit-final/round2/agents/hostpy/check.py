#!/usr/bin/env python3
"""Invariants of a hostpy run (read-only over the run's databases; replays through the engine).

I1 effects       every irreversible external effect at most once per logical action; a call
                 whose processed outcome is `failed` or negative has no effect; a positive outcome
                 belongs to an effect of the same logical action
I2 outcomes      every call with a token in the current history got exactly one processed outcome
                 event; not accepted only when its wait was legitimately replaced/cleared/lost;
                 no accepted outcome of a call from another history (run id)
I3 stuck         no wait left in a snapshot at quiescence, no snapshot timer without a scheduled
                 or processed firing; users on a waiting message without a wait are listed
I4 replay        each delivery processed at most once; every step read the snapshot the previous
                 step wrote; the final snapshot equals a sequential replay through the engine
I5 background    no event waited for a background action; steps ran while background actions
                 were pending (non-vacuous)
I6 protocol      no protocol violation seen by the client; every logged reply validates
I7 business      no condition computed with sequential actions pending; no order with an empty
                 cart; no overdraft
I8 outbox        nothing left unexecuted; the failure queue holds only background actions
"""
from __future__ import annotations

import collections
import concurrent.futures
import json
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("FIGRAM_REPO", "/home/user/figram-audit-worktrees/a6-hostpy")
sys.path.insert(0, os.path.join(REPO, "clients", "python"))
sys.path.insert(0, HERE)
from figram_client import Engine  # noqa: E402
import protocol  # noqa: E402
from host import Doc, AMOUNT, BALANCE  # noqa: E402

POSITIVE = {"crm": "created", "bank": "sent", "venue": "booked", "email": "delivered", "notify": "ok"}
SERVICE_OF = {"create_order": "crm", "transfer": "bank", "book_seat": "venue", "send_receipt": "email",
              "send_confirmation": "email", "notify_staff": "notify", "nudge_user": "notify"}


def ro(path):
    c = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=60)
    c.row_factory = sqlite3.Row
    return c


def check(run_dir: str, docs: dict[int, str], engine_bin: str, expect: dict | None = None,
          replay: bool = True) -> dict:
    H = ro(os.path.join(run_dir, "host.db"))
    X = ro(os.path.join(run_dir, "ext.db"))
    M = ro(os.path.join(run_dir, "metrics.db"))
    sched_path = os.path.join(run_dir, "sched.db")
    S = ro(sched_path) if os.path.exists(sched_path) else H
    metrics = {r["k"]: r["v"] for r in M.execute("SELECT k, v FROM m")}
    alerts = collections.Counter(r["kind"] for r in M.execute("SELECT kind FROM alerts"))
    dox = {v: Doc(p) for v, p in docs.items()}
    res: dict = {"metrics": metrics, "alerts": dict(alerts), "violations": {}, "notes": {}}
    V = res["violations"]

    def viol(name, example):
        v = V.setdefault(name, {"count": 0, "examples": []})
        v["count"] += 1
        if len(v["examples"]) < 5:
            v["examples"].append(example)

    elog = [dict(r) for r in H.execute("SELECT * FROM elog ORDER BY id")]
    by_user = collections.defaultdict(list)
    for e in elog:
        by_user[e["user"]].append(e)
    outbox = [dict(r) for r in H.execute("SELECT * FROM outbox ORDER BY id")]
    snaps = {r["user"]: json.loads(r["snap"]) for r in H.execute("SELECT user, snap FROM snapshots")}
    snap_docv = {r["user"]: r["docv"] for r in H.execute("SELECT user, docv FROM snapshots")}
    effects = [dict(r) for r in X.execute("SELECT * FROM effects")]
    for e in effects:
        e["p"] = json.loads(e["payload"])
    res["notes"]["users"] = len(by_user)
    res["notes"]["elog"] = len(elog)
    res["notes"]["steps"] = sum(1 for e in elog if e["op"] in ("step", "start", "restart"))
    res["notes"]["calls_with_token"] = sum(1 for o in outbox if o["kind"] == "call" and o["token"] is not None)

    # ---------------- I1 effects ----------------
    pos_by_logical = collections.defaultdict(list)
    for e in effects:
        if e["result"] == POSITIVE.get(e["service"]):
            pos_by_logical[(e["service"], e["p"].get("logical"))].append(e["key"])
    for (svc, logical), keys in pos_by_logical.items():
        if len(keys) > 1:
            viol("I1.duplicate_effect", {"service": svc, "logical": logical, "keys": keys})
    outcome_entry = {}
    for e in elog:
        if e["kind"] == "outcome" and e["op"] in ("step", "start", "drop"):
            outcome_entry.setdefault((e["user"], e["uid"]), []).append(e)
    eff_by_key = {(e["service"], e["key"]): e for e in effects}
    for o in outbox:
        if o["kind"] != "call" or o["token"] is None:
            continue
        uid = f"call:{o['name']}:{o['token']}:{o['run']}"
        ents = [x for x in outcome_entry.get((o["user"], uid), []) if x["op"] != "drop" or x["note"] == "no_snapshot"]
        if not ents:
            continue
        ev = ents[0]["event"]
        outcome = ev[ev.rindex(":") + 1:]
        svc = SERVICE_OF[o["name"]]
        pos = pos_by_logical.get((svc, o["logical"]), [])
        if outcome == POSITIVE[svc]:
            eff = eff_by_key.get((svc, o["key"]))
            if eff is None or eff["p"].get("logical") != o["logical"]:
                viol("I1.positive_outcome_of_another_action",
                     {"user": o["user"], "call": uid, "key": o["key"],
                      "effect_logical": eff and eff["p"].get("logical"), "row_logical": o["logical"]})
        elif pos:
            viol("I1.told_" + outcome + "_but_acted", {"user": o["user"], "call": uid, "effect_keys": pos})
    if any(e["service"] == "crm" for e in effects):
        for e in effects:
            if e["service"] == "crm" and e["result"] == "created" and not e["p"].get("items"):
                viol("I7.empty_order", {"key": e["key"]})
    bank = collections.Counter()
    for e in effects:
        if e["service"] == "bank" and e["result"] == "sent":
            bank[e["user"]] += AMOUNT
    for u, s in bank.items():
        if s > BALANCE:
            viol("I7.overdraft", {"user": u, "sent": s})

    # ---------------- I2 outcomes ----------------
    step_of_src = {}
    for e in elog:
        if e["op"] in ("step", "start", "restart"):
            step_of_src[(e["user"], e["uid"])] = e["id"]
    rows_by_user_name = collections.defaultdict(list)
    for o in outbox:
        if o["kind"] == "call" and o["token"] is not None:
            rows_by_user_name[(o["user"], o["name"])].append(o)
    accepted = ignored = 0
    classes = collections.Counter()
    for o in outbox:
        if o["kind"] != "call" or o["token"] is None:
            continue
        uid = f"call:{o['name']}:{o['token']}:{o['run']}"
        ents = outcome_entry.get((o["user"], uid), [])
        if o["state"] == "new":
            viol("I2.call_not_executed", {"user": o["user"], "call": uid})
            continue
        if len(ents) == 0:
            viol("I2.outcome_never_processed", {"user": o["user"], "call": uid, "state": o["state"]})
            continue
        if len(ents) > 1:
            viol("I2.outcome_processed_twice", {"user": o["user"], "call": uid, "n": len(ents)})
        e = ents[0]
        if e["op"] == "drop":
            classes["dropped:" + (e["note"] or "")] += 1
            if e["note"] != "no_snapshot":
                viol("I2.outcome_of_current_call_dropped", {"user": o["user"], "call": uid, "note": e["note"]})
            continue
        if e["accepted"]:
            accepted += 1
            classes["accepted"] += 1
            continue
        ignored += 1
        issued = step_of_src.get((o["user"], o["src"]), 0)
        why = None
        for later in rows_by_user_name[(o["user"], o["name"])]:
            if later["token"] > o["token"] and step_of_src.get((o["user"], later["src"]), 10**18) < e["id"]:
                why = "replaced_by_later_call"
                break
        if why is None:
            for x in by_user[o["user"]]:
                if issued < x["id"] < e["id"] and x["op"] in ("restart", "lose"):
                    why = "cleared_by_" + x["op"]
                    break
        if why is None and e["before"] is None:
            why = "no_snapshot"
        if why is None:
            for x in by_user[o["user"]]:
                if issued < x["id"] < e["id"] and x["op"] in ("step", "start") and x["kind"] == "outcome" \
                        and x["event"] and x["event"].startswith(f"call:{o['name']}:") and x["token"] == o["token"] \
                        and x["accepted"]:
                    why = "answered_by_other_delivery"
                    break
        if why is None:
            # a wait that the step that issued it did not record (an `every` replaced by a doc update…)
            wait_there = False
            for x in by_user[o["user"]]:
                if x["id"] == issued and x["after"]:
                    wait_there = any(w["call"] == o["name"] and w["token"] == o["token"]
                                     for w in json.loads(x["after"]).get("pending", []))
            why = "never_waited" if not wait_there else None
        if why is None:
            viol("I2.outcome_not_accepted_without_reason", {"user": o["user"], "call": uid, "entry": e["id"]})
        else:
            classes[why] += 1
    start_runs = {(o["user"], o["name"], o["token"]): o["run"] for o in outbox if o["kind"] == "start_timer"}
    for e in elog:
        if e["kind"] == "outcome" and e["op"] == "step" and e["accepted"]:
            name = e["event"][len("call:"):e["event"].rindex(":")]
            cur = [o for o in rows_by_user_name[(e["user"], name)] if o["token"] == e["token"]]
            if [o["run"] for o in cur] != [e["run"]]:
                viol("I2.accepted_outcome_of_another_history",
                     {"user": e["user"], "uid": e["uid"], "current_runs": [o["run"] for o in cur]})
        if e["kind"] == "timer" and e["op"] == "step" and e["run"]:
            name = e["event"][len("timer:"):]
            cur = start_runs.get((e["user"], name, e["token"]))
            if cur is not None and cur != e["run"]:
                viol("I2.timer_of_another_history_processed", {"user": e["user"], "uid": e["uid"]})
    res["notes"]["outcomes"] = dict(classes)

    # ---------------- I3 stuck ----------------
    sched = {(r["user"], r["name"], r["token"]) for r in S.execute("SELECT user, name, token FROM timers")}
    processed = {(r["user"], r["uid"]) for r in H.execute("SELECT user, uid FROM processed")}
    stuck_wait = []
    for u, s in snaps.items():
        if s.get("pending"):
            viol("I3.wait_left_in_snapshot", {"user": u, "pending": s["pending"], "at": s["at"]})
        d = dox[snap_docv[u]]
        if s["at"] in d.waiting and not s.get("pending"):
            stuck_wait.append(u)
        for name, tok in s.get("timers", {}).items():
            if (u, name, tok) in sched:
                viol("I3.not_quiescent_timer_scheduled", {"user": u, "timer": name, "token": tok})
                continue
            run = start_runs.get((u, name, tok))
            if run is None or (u, f"timer:{name}:{tok}:{run}") not in processed:
                viol("I3.timer_lost", {"user": u, "timer": name, "token": tok, "at": s["at"], "run": run})
    # every started timer either fired (its firing processed) or left the snapshot before it was due
    slack = (expect or {}).get("timer_slack_s", 3.0)
    refused = collections.defaultdict(list)
    for r in M.execute("SELECT user, detail FROM alerts WHERE kind='start_refused'"):
        refused[r["user"]].append(json.loads(r["detail"])[:2])
    fired_or_dropped = processed
    never = 0
    for o in outbox:
        if o["kind"] != "start_timer" or o["state"] != "done":
            continue
        u, name, tok = o["user"], o["name"], o["token"]
        if (u, f"timer:{name}:{tok}:{o['run']}") in fired_or_dropped:
            continue
        due = o["created"] + o["ms"] / 1000.0
        held_until = None
        for x in by_user[u]:
            if x["at"] is None or x["at"] <= o["created"] or x["op"] not in ("step", "start", "restart", "lose"):
                continue
            a = json.loads(x["after"]) if x["after"] else {}
            if a.get("timers", {}).get(name) != tok:
                held_until = x["at"]
                break
        if held_until is None:
            held_until = float("inf")          # still in the final snapshot (also checked above)
        if held_until > due + slack:
            never += 1
            if [name, tok] in refused.get(u, []):
                viol("I3.timer_never_fired:start_refused_by_pair_rule", {"user": u, "timer": name, "token": tok})
            viol("I3.timer_never_fired", {"user": u, "timer": name, "token": tok, "due": round(due, 2),
                                          "held_until": held_until if held_until != float("inf") else "end",
                                          "run": o["run"]})
    res["notes"]["users_on_waiting_message_without_wait"] = len(stuck_wait)
    stuck_classes = collections.Counter()
    stuck_examples = {}
    for u in stuck_wait:
        at = snaps[u]["at"]
        waited = dox[snap_docv[u]].waiting[at]
        entry = None
        for x in by_user[u]:
            if x["op"] in ("step", "start", "restart") and x["after"]:
                a = json.loads(x["after"])["at"]
                b = json.loads(x["before"])["at"] if x["before"] else None
                if a == at and (b != at or any(act.get("call") == waited for act in json.loads(x["actions"]))):
                    entry = x
        if entry is None:
            cls = "unknown_entry"
        elif not any(act.get("call") == waited for act in json.loads(entry["actions"])):
            snap_after = json.loads(entry["after"])
            how = "recovered" if entry["recovered"] else (
                "resume_or_back" if entry["kind"] == "tg" else "other")
            cls = f"doc_trap:entered_without_the_call({how})"
        else:
            cls = "entered_with_the_call"
        stuck_classes[cls] += 1
        stuck_examples.setdefault(cls, []).append({"user": u, "at": at, "entry": entry and entry["id"],
                                                    "event": entry and entry["event"]})
        if not cls.startswith("doc_trap") and not (expect or {}).get("stuck_ok"):
            viol("I3.user_on_waiting_message_without_wait", {"user": u, "at": at, "class": cls})
    res["notes"]["stuck_classes"] = dict(stuck_classes)
    res["notes"]["stuck_examples"] = {k: v[:3] for k, v in stuck_examples.items()}

    # ---------------- I4 dedupe and replay ----------------
    seen = collections.Counter((e["user"], e["uid"]) for e in elog if e["op"] in ("step", "start", "restart", "drop", "lose"))
    for k, n in seen.items():
        if n > 1:
            viol("I4.delivery_processed_twice", {"user": k[0], "uid": k[1], "n": n})
    if replay:
        engines = {v: Engine(engine_bin, p, processes=2, timeout=30) for v, p in docs.items()}
        try:
            def replay_user(u):
                out = []
                prev = None
                for e in by_user[u]:
                    op = e["op"]
                    if op == "outbox_dup":
                        continue
                    b = json.loads(e["before"]) if e["before"] else None
                    if op in ("lose", "drop", "step", "restart") and b != prev:
                        out.append(("I4.step_read_a_stale_snapshot", {"user": u, "entry": e["id"], "op": op}))
                    if op in ("lose",):
                        prev = None
                        continue
                    if op == "drop":
                        continue
                    g = json.loads(e["guards"]) if e["guards"] else {}

                    def guards(names, g=g):
                        return {n: g[n] for n in names}
                    eng = engines[e["docv"]]
                    try:
                        if op == "start":
                            if prev is not None:
                                out.append(("I4.start_over_existing_snapshot", {"user": u, "entry": e["id"]}))
                            r = eng.start(u, e["seqarg"], e["event"], guards)
                        elif op == "restart":
                            r = eng.restart(u, prev)
                        else:
                            r = eng.step(u, prev, e["event"], guards, e["token"])
                    except Exception as ex:  # noqa: BLE001
                        out.append(("I4.replay_failed", {"user": u, "entry": e["id"], "error": str(ex)[:200]}))
                        prev = json.loads(e["after"]) if e["after"] else prev
                        continue
                    a = json.loads(e["after"])
                    if r["snapshot"] != a or r["actions"] != json.loads(e["actions"]):
                        out.append(("I4.replay_differs", {"user": u, "entry": e["id"], "logged": a,
                                                          "replayed": r["snapshot"]}))
                    try:
                        protocol.check_snapshot(a)
                        protocol.check_actions(json.loads(e["actions"]))
                    except protocol.ProtocolError as ex:
                        out.append(("I6.logged_reply_invalid", {"user": u, "entry": e["id"], "error": str(ex)}))
                    prev = a
                if prev != snaps.get(u):
                    out.append(("I4.final_snapshot_differs", {"user": u, "replayed": prev, "stored": snaps.get(u)}))
                return out
            with concurrent.futures.ThreadPoolExecutor(8) as pool:
                for out in pool.map(replay_user, sorted(by_user)):
                    for name, ex in out:
                        viol(name, ex)
        finally:
            for e in engines.values():
                e.close()
        res["notes"]["replayed_entries"] = sum(1 for e in elog if e["op"] in ("step", "start", "restart"))

    # ---------------- I5 background ----------------
    if metrics.get("barrier.blocked_by_background", 0) > 0:
        viol("I5.event_waited_for_background_action", {"times": metrics["barrier.blocked_by_background"]})
    res["notes"]["steps_with_background_pending"] = sum(1 for e in elog if (e["bg_pending"] or 0) > 0)

    # ---------------- I6 protocol ----------------
    if metrics.get("engine.protocol_violation", 0) > 0:
        viol("I6.protocol_violation", {"times": metrics["engine.protocol_violation"]})

    # ---------------- I7 conditions ----------------
    if metrics.get("cond.with_pending_sequential", 0) > 0:
        viol("I7.condition_with_pending_sequential_actions", {"times": metrics["cond.with_pending_sequential"]})

    # ---------------- I8 outbox ----------------
    for o in outbox:
        if o["state"] == "new":
            viol("I8.action_not_executed", {"user": o["user"], "kind": o["kind"], "name": o["name"], "id": o["id"]})
        if o["state"] == "failq" and not o["detached"]:
            viol("I8.sequential_action_in_failure_queue", {"user": o["user"], "name": o["name"]})
    n_in = H.execute("SELECT COUNT(*) FROM inbound").fetchone()[0]
    if n_in:
        viol("I8.inbound_not_drained", {"rows": n_in})
    for r in H.execute("SELECT * FROM poison"):
        viol("I8.poison_event", dict(r))
    if metrics.get("deadlock.outcome_inline", 0) > 0:
        viol("I8.executor_deadlocked_on_user_lock", {"times": metrics["deadlock.outcome_inline"]})
    res["notes"]["failq"] = H.execute("SELECT COUNT(*) FROM failq").fetchone()[0]
    res["notes"]["effects_positive"] = sum(1 for e in effects if e["result"] == POSITIVE.get(e["service"]))
    res["notes"]["applications"] = X.execute("SELECT COUNT(*) FROM applications").fetchone()[0]
    res["ok"] = not V
    return res


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir")
    ap.add_argument("--doc", action="append", required=True, help="VERSION=PATH")
    ap.add_argument("--engine", default=os.path.join(REPO, ".lake/build/bin/figram-engine"))
    a = ap.parse_args()
    docs = {int(x.split("=", 1)[0]): x.split("=", 1)[1] for x in a.doc}
    r = check(a.run_dir, docs, a.engine)
    print(json.dumps({k: v for k, v in r.items() if k != "metrics"}, indent=1, default=str)[:20000])
    sys.exit(0 if r["ok"] else 1)

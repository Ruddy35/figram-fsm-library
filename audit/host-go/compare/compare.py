#!/usr/bin/env python3
"""Go-vs-Python comparison of the production-like hosts (HOSTS_SPEC output format).

Normalization (defined BEFORE looking at the data, see REPORT host-go §comparison):
  N1  update_id "reset:<x>" -> "reset": the id of a reset is runner-local (Python: its
      delivery counter d, Go: the corpus line number); the transition itself is compared.
  N2  a transition is compared on (update_id, event, arg, token, kind, seq, at, t_ms,
      actions, recovered); actions are compared as JSON values (key order irrelevant).
      t_ms is the simulated clock -> deterministic, compared exactly.
  N3  final snapshot compared as a JSON value (key order irrelevant), plus doc.
  N4  effects: sets of (key, user, "call", crm-op) and (key, user, "send", text);
      Telegram duplicates are timing-dependent (at-least-once) -> deduplicated by key.
  N5  booking: which of several concurrent contenders wins a scarce slot is timing-
      dependent (shared/README). Users whose sequences differ are accepted only if
      they all took part in a contended block (>=2 confirmations of the same slot
      between two barriers) AND winner-invariant aggregates agree: multiset of final
      `at`, active reservations per slot (from reserve/cancel actions), number of
      transitions, effects per CRM op / send text.
  N6  counts that depend on timing (NACKs, engine retries, Telegram duplicates,
      latency) are reported, never compared.
Every remaining difference is printed. Exit 1 if any unexplained difference.

    python3 -I compare.py GO_DIR PY_DIR CORPUS_DIR [--json OUT]
"""
import json
import os
import sys
from collections import Counter


def norm_uid(u):
    return "reset" if isinstance(u, str) and u.startswith("reset:") else u


def canon(v):
    return json.dumps(v, sort_keys=True, ensure_ascii=False)


def ntrans(t):
    return (norm_uid(t.get("update_id")), t.get("event"), t.get("arg"), t.get("token"), t.get("kind"),
            t.get("seq"), t.get("at"), t.get("t_ms"), canon(t.get("actions")), bool(t.get("recovered")))


def effects_set(d):
    out = set()
    for e in d.get("effects") or []:
        if "call" in e:
            out.add((e["key"], e["user"], "call", e["call"]))
        else:
            out.add((e["key"], e["user"], "send", e["send"]))
    return out


def contended_users(corpus_path):
    """Users that confirmed a slot together with others between two barriers."""
    users, block, sel = set(), [], {}
    for line in open(corpus_path):
        it = json.loads(line)
        if "ctl" in it:
            groups = {}
            for u in block:
                groups.setdefault(sel.get(u), set()).add(u)
            for s, us in groups.items():
                if len(us) >= 2:
                    users |= us
            block = []
            continue
        if it.get("event") == "btn:slot":
            sel[it["user"]] = it.get("arg")
        if it.get("event") == "btn:confirm":
            block.append(it["user"])
    groups = {}
    for u in block:
        groups.setdefault(sel.get(u), set()).add(u)
    for s, us in groups.items():
        if len(us) >= 2:
            users |= us
    return users


def booking_aggregates(d, users):
    at = Counter()
    active = set()
    ntr = 0
    for u in sorted(users):
        x = d["users"].get(u)
        if not x:
            continue
        at[(x.get("snapshot") or {}).get("at")] += 1
        ntr += len(x["transitions"])
        sel = None
        for t in x["transitions"]:
            for a in t["actions"]:
                c = a.get("call")
                if c == "choose_slot":
                    sel = t.get("arg")
                elif c == "release_hold":
                    sel = None
                elif c == "reserve_slot":
                    active.add((u, sel))
                elif c == "cancel_booking":
                    active = {p for p in active if p[0] != u}
    per_slot = Counter(s for _, s in active)
    eff = Counter()
    for e in d.get("effects") or []:
        if e["user"] in users:
            eff[e.get("call") or ("send:" + e.get("send", ""))] += 1
    return {"final_at": dict(sorted(at.items(), key=lambda kv: str(kv[0]))), "active_per_slot": dict(sorted(per_slot.items())),
            "transitions": ntr, "effects": dict(sorted(eff.items()))}


def compare(go, py, corpus_path):
    diffs, notes = [], []
    gu, pu = go["users"], py["users"]
    if sorted(gu) != sorted(pu):
        diffs.append(f"user sets differ: only go {sorted(set(gu)-set(pu))}, only py {sorted(set(pu)-set(gu))}")
    differing = []
    same = 0
    for u in sorted(set(gu) & set(pu)):
        a, b = gu[u], pu[u]
        ta = [ntrans(t) for t in a["transitions"]]
        tb = [ntrans(t) for t in b["transitions"]]
        ok = ta == tb and canon(a.get("snapshot")) == canon(b.get("snapshot")) and a.get("doc") == b.get("doc")
        if ok:
            same += 1
            continue
        differing.append(u)
        first = None
        for i, (x, y) in enumerate(zip(ta, tb)):
            if x != y:
                first = (i, x, y)
                break
        if first is None and len(ta) != len(tb):
            first = (min(len(ta), len(tb)), ta[len(tb):len(tb)+1] or None, tb[len(ta):len(ta)+1] or None)
        detail = {"user": u, "go_n": len(ta), "py_n": len(tb),
                  "go_final": a.get("snapshot"), "py_final": b.get("snapshot"),
                  "first_diff": None if first is None else {"index": first[0], "go": first[1], "py": first[2]}}
        differing[-1] = detail
    eg, ep = effects_set(go), effects_set(py)
    eff_diff = {"only_go": sorted(eg - ep)[:20], "only_py": sorted(ep - eg)[:20], "n_only_go": len(eg - ep),
                "n_only_py": len(ep - eg)}
    scen = go.get("scenario")
    explained = False
    if scen == "booking" and differing:
        cont = contended_users(corpus_path)
        bad_users = [d["user"] for d in differing if d["user"] not in cont]
        ag, ap = booking_aggregates(go, cont), booking_aggregates(py, cont)
        if not bad_users and canon(ag) == canon(ap):
            explained = True
            notes.append(f"N5 booking: {len(differing)} contended users differ (race winners), aggregates equal: {ag}")
        else:
            diffs.append(f"N5 booking aggregates/users not explained: non-contended differing {bad_users}; go {ag} py {ap}")
    if differing and not explained:
        for d in differing:
            diffs.append(d)
    if (eff_diff["n_only_go"] or eff_diff["n_only_py"]):
        if explained:
            notes.append({"effects_differ_due_to_race": eff_diff})
        else:
            diffs.append({"effects": eff_diff})
    return {"scenario": scen, "users_equal": same, "users_total": len(set(gu) | set(pu)),
            "effects_go": len(eg), "effects_py": len(ep), "differences": diffs, "notes": notes,
            "invariants_go": {k: v for k, v in go.get("invariants", {}).items() if v != "PASS"},
            "invariants_py": {k: v for k, v in py.get("invariants", {}).items() if v != "PASS"},
            "timing_counts": {
                "go": {k: go["counts"].get(k) for k in ("nacks", "crashes", "host_restarts", "repairs", "recovered", "rejected")},
                "py": {k: py["counts"].get(k) for k in ("nacks", "crashes", "host_restarts", "repairs", "recovered", "rejected")}}}


def main():
    gdir, pdir, cdir = sys.argv[1], sys.argv[2], sys.argv[3]
    out = None
    if "--json" in sys.argv:
        out = sys.argv[sys.argv.index("--json") + 1]
    results = {}
    bad = 0
    names = sorted(f[:-5] for f in os.listdir(pdir) if f.endswith(".json") and f != "red_matrix.json")
    for n in names:
        gp, pp = os.path.join(gdir, n + ".json"), os.path.join(pdir, n + ".json")
        if not os.path.exists(gp):
            results[n] = {"missing_go": True}
            print(f"{n:16s} MISSING go output")
            bad += 1
            continue
        go, py = json.load(open(gp)), json.load(open(pp))
        r = compare(go, py, os.path.join(cdir, n + ".jsonl"))
        results[n] = r
        status = "EQUAL" if not r["differences"] else "DIFF"
        if r["differences"]:
            bad += 1
        print(f"{n:16s} {status:5s} users {r['users_equal']}/{r['users_total']} effects go={r['effects_go']} py={r['effects_py']}"
              f" notes={len(r['notes'])} diffs={len(r['differences'])}")
        for d in r["differences"][:6]:
            print("    DIFF", json.dumps(d, ensure_ascii=False)[:600])
        for d in r["notes"]:
            print("    NOTE", json.dumps(d, ensure_ascii=False)[:600])
    if out:
        with open(out, "w") as f:
            json.dump(results, f, ensure_ascii=False, indent=1)
    print(f"scenarios: {len(names)}, with unexplained differences: {bad}")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()

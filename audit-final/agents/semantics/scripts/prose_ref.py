#!/usr/bin/env python3
"""A reference interpreter of docs/format.md sections 7-8 written from the prose (decisions 1-13,
rules of a step, of `start` with an event, of `restart`), and a random differential test against
figram-engine.

    prose_ref.py SEED COUNT        run COUNT random documents (each with many requests)

For every document: `figram check` must accept it (otherwise it is skipped and counted), then a
batch of random requests is run through figram-engine and through this interpreter; replies are
compared as JSON values (object key order ignored, arrays in order; errors by `code`).
"""
import json, random, subprocess, sys, os, tempfile

BIN = "/home/user/figram-audit-worktrees/a5-semantics/.lake/build/bin"
MAX = 2**53 - 1


class Doc:
    def __init__(self, d):
        self.d = d
        self.calls = {}
        for n, c in d.get("calls", {}).items():
            if isinstance(c, str):
                c = {"text": c}
            outs = c.get("outcomes", [])
            wait = "free" if not outs else ("every" if c.get("every") else "latest")
            self.calls[n] = (bool(c.get("detached", False)), wait)
        self.parent, self.kind, self.initial, self.on, self.timers = {}, {}, {}, {}, {}
        self.order = []
        self._walk("", d["bot"], None)
        self.messages = [n for n in self.order if self.kind[n] == "msg"]
        self.hist = set()
        for n in self.order:
            for ev, vs in self.on[n].items():
                for v in vs:
                    if v.get("resume") and "to" in v:
                        self.hist.add(v["to"])

    def _walk(self, name, node, parent):
        self.order.append(name)
        self.parent[name] = parent
        self.on[name] = node.get("on", {})
        if "children" in node:
            self.kind[name] = "sec"
            self.initial[name] = node["initial"]
            self.timers[name] = {}
            for cn, c in node["children"].items():
                self._walk(cn, c, name)
        else:
            self.kind[name] = "msg"
            self.timers[name] = node.get("timers", {})

    def anc(self, n):
        out = []
        while n is not None:
            out.append(n)
            n = self.parent[n]
        return out

    def enter(self, n):
        while self.kind.get(n) == "sec":
            n = self.initial[n]
        return n

    @property
    def init_msg(self):
        return self.enter("")

    def entry(self, T):
        acts = []
        for s in self.anc(T)[1:]:
            if s in self.hist:
                acts.append(("remember", s, T))
        for t, ms in self.timers[T].items():
            acts.append(("start", t, ms))
        acts.append(("send", T))
        return acts

    def exit(self, L):
        return [("cancel", t) for t in self.timers.get(L, {})]

    def call_act(self, n):
        det, wait = self.calls.get(n, (False, "free"))
        return ("call", n, det, wait)

    def cands(self, L, key):
        out = []
        for x in self.anc(L):
            out.extend(self.on[x].get(key, []))
        return out


def target(v):
    if "to" not in v:
        return ("stay",)
    t = ("resume", v["to"]) if v.get("resume") else ("node", v["to"])
    return ("back", t) if v.get("back") else t


class Snap:
    def __init__(self, at, back=None, resume=None, timers=None, pending=None, seq=0):
        self.at, self.back = at, back
        self.resume = dict(resume or {})
        self.timers = dict(timers or {})
        self.pending = [tuple(p) for p in (pending or [])]
        self.seq = seq

    def copy(self):
        return Snap(self.at, self.back, self.resume, self.timers, list(self.pending), self.seq)

    def enc(self):
        o = {"at": self.at}
        if self.back is not None:
            o["back"] = self.back
        if self.resume:
            o["resume"] = dict(self.resume)
        if self.timers:
            o["timers"] = dict(self.timers)
        if self.pending:
            o["pending"] = [{"call": c, "token": t} for c, t in self.pending]
        if self.seq != 0:
            o["seq"] = self.seq
        return o

    @staticmethod
    def dec(o):
        return Snap(o["at"], o.get("back"), o.get("resume"), o.get("timers"),
                    [(p["call"], p["token"]) for p in o.get("pending", [])], o.get("seq", 0))


def accepts(s, ev):
    k = ev[0]
    if k == "named":
        return True
    if k == "timer":
        return s.timers.get(ev[1]) == ev[2]
    return (ev[1], ev[3]) in s.pending


def settle(s, ev):
    s = s.copy()
    if ev[0] == "outcome":
        s.pending = [p for p in s.pending if p != (ev[1], ev[3])]
    return s


def key(ev):
    if ev[0] == "named":
        return ev[1]
    if ev[0] == "timer":
        return "timer:" + ev[1]
    return "call:" + ev[1] + ":" + ev[2]


def realize(D, s, T, acts):
    s = s.copy()
    tok = s.seq + 1
    s.at, s.seq = T, tok
    out = []
    for a in acts:
        if a[0] == "remember":
            s.resume[a[1]] = a[2]
        elif a[0] == "start":
            s.timers[a[1]] = tok
            out.append({"start_timer": a[1], "ms": a[2], "token": tok})
        elif a[0] == "cancel":
            s.timers.pop(a[1], None)
            out.append({"cancel_timer": a[1]})
        elif a[0] == "call":
            _, n, det, wait = a
            o = {"call": n}
            if wait != "free":
                o["token"] = tok
                if wait == "latest":
                    s.pending = [(n, tok)] + [p for p in s.pending if p[0] != n]
                else:
                    s.pending = [(n, tok)] + [p for p in s.pending if p != (n, tok)]
            if det:
                o["detached"] = True
            out.append(o)
        elif a[0] == "send":
            out.append({"send": a[1]})
    return s, out


def resolve(D, s, t):
    if t[0] == "node":
        return D.enter(t[1])
    if t[0] == "resume":
        sec = t[1]
        v = s.resume.get(sec)
        if v is not None and v in D.messages and sec in D.anc(v) and v != sec:
            return v
        return D.enter(sec)
    if t[0] == "back":
        if s.back is not None and s.back in D.messages:
            return s.back
        return resolve(D, s, t[1])
    return s.at


def guard_ok(env, v):
    return "if" not in v or env.get(v["if"], False)


def fire(D, s, v):
    T = resolve(D, s, target(v))
    s = s.copy()
    if D.parent.get(s.at) != D.parent.get(T):
        s.back = s.at
    ex = D.exit(s.at)
    stale = [("cancel", t) for t in s.timers if ("cancel", t) not in ex]
    acts = ex + stale + [D.call_act(n) for n in v.get("do", [])] + D.entry(T)
    return realize(D, s, T, acts)


def enter_bot(D, s, pre, ev, env):
    b = None
    if accepts(s, ev) and ev[0] != "timer":
        b = next((v for v in D.cands(D.init_msg, key(ev)) if guard_ok(env, v)), None)
    s2 = settle(s, ev)
    if b is not None:
        tmp = s.copy(); tmp.at = D.init_msg
        T = resolve(D, tmp, target(b))
        return realize(D, s2, T, pre + [D.call_act(n) for n in b.get("do", [])] + D.entry(T))
    return realize(D, s2, D.init_msg, pre + D.entry(D.init_msg))


def owns(D, s, ev):
    return ev[0] != "timer" or ev[1] in D.timers.get(s.at, {})


def step(D, s, ev, env):
    if s.at not in D.messages:
        return enter_bot(D, s, [("cancel", t) for t in s.timers], ev, env)
    if not (accepts(s, ev) and owns(D, s, ev)):
        return s, []
    b = next((v for v in D.cands(s.at, key(ev)) if guard_ok(env, v)), None)
    if b is None:
        return settle(s, ev), []
    if "to" not in b:
        s2 = settle(s, ev)
        if ev[0] == "timer":
            s2.timers.pop(ev[1], None)
        return realize(D, s2, s2.at, [D.call_act(n) for n in b.get("do", [])])
    return fire(D, settle(s, ev), b)


def needed(vs, given):
    out = []
    for v in vs:
        if "if" not in v:
            break
        g = v["if"]
        if g in given:
            if given[g]:
                break
            continue
        if g not in out:
            out.append(g)
    return out


def missing(D, s, ev, given):
    if s.at not in D.messages:
        if ev[0] == "timer":
            return []
        if ev[0] == "outcome" and not accepts(s, ev):
            return []
        return needed(D.cands(D.init_msg, key(ev)), given)
    if accepts(s, ev) and owns(D, s, ev):
        return needed(D.cands(s.at, key(ev)), given)
    return []


def parse_event(req):
    e = req["event"]
    if e.startswith("timer:"):
        return ("timer", e[6:], req["token"])
    if e.startswith("call:"):
        body = e[5:]
        c, o = body.rsplit(":", 1)
        return ("outcome", c, o, req["token"])
    return ("named", e)


def reply(D, req):
    rid = req.get("id")
    op = req["op"]
    if op == "hello":
        return {"id": rid, "protocol": 3, "states": len(D.messages)}
    env = req.get("guards", {})
    if op == "start":
        n = req.get("seq", 0)
        if "event" in req:
            ev = ("named", req["event"])
            miss = needed(D.cands(D.init_msg, req["event"]), env)
            if miss:
                return {"id": rid, "need": miss}
            s, acts = enter_bot(D, Snap("", seq=n), [], ev, env)
        else:
            s, acts = realize(D, Snap("", seq=n), D.init_msg, D.entry(D.init_msg))
        if s.seq > MAX:
            return {"id": rid, "code": "seq_overflow"}
        return {"id": rid, "snapshot": s.enc(), "actions": acts}
    if op == "restart":
        s = Snap.dec(req["snapshot"])
        pre = [("cancel", t) for t in s.timers]
        s.resume, s.back, s.pending = {}, None, []
        s.timers = {}
        s2, acts = realize(D, s, D.init_msg, pre + D.entry(D.init_msg))
        if s2.seq > MAX:
            return {"id": rid, "code": "seq_overflow"}
        return {"id": rid, "snapshot": s2.enc(), "actions": acts}
    s = Snap.dec(req["snapshot"])
    ev = parse_event(req)
    miss = missing(D, s, ev, env)
    if miss:
        return {"id": rid, "need": miss}
    s2, acts = step(D, s, ev, env)
    if s2.seq > MAX:
        return {"id": rid, "code": "seq_overflow"}
    r = {"id": rid, "snapshot": s2.enc(), "actions": acts}
    if s.at not in D.messages:
        r["recovered"] = True
    return r


# ---------------------------------------------------------------- random documents and requests

def gen_doc(R):
    nmsg = R.randint(3, 6)
    msgs = [f"m{i}" for i in range(nmsg)]
    secs = []
    # structure: root holds some messages and up to 2 sections; a section may hold a subsection
    tree = {"": {"kids": []}}
    pool = list(msgs)
    R.shuffle(pool)
    nsec = R.randint(0, 2)
    containers = [""]
    for i in range(nsec):
        sname = f"S{i}"
        par = R.choice(containers)
        tree[sname] = {"kids": []}
        tree[par]["kids"].append(sname)
        containers.append(sname)
        secs.append(sname)
    for m in pool:
        tree[R.choice(containers)]["kids"].append(m)
    # every section needs a child; give empty ones a message
    for c in containers:
        if not tree[c]["kids"]:
            m = f"m{len(msgs)}"; msgs.append(m); tree[c]["kids"].append(m)
    timers = {}
    for m in msgs:
        if R.random() < 0.4:
            timers[m] = {f"t{m}{j}": R.choice([0, 5, 100, 7200000]) for j in range(R.randint(1, 2))}
    shared = "tx"  # a timer name shared by two messages
    if len(msgs) > 1 and R.random() < 0.5:
        for m in R.sample(msgs, 2):
            timers.setdefault(m, {})[shared] = 10
    calls = {"p": "Plain", "bg": {"text": "Bg", "detached": True},
             "L": {"text": "Latest", "outcomes": ["ok", "no"]},
             "E": {"text": "Every", "outcomes": ["r"], "every": True},
             "DL": {"text": "Detached latest", "outcomes": ["r"], "detached": True},
             "n:s": {"text": "Colon", "outcomes": ["ok"]}}
    outcome_events = ["call:L:ok", "call:L:no", "call:L:failed", "call:E:r", "call:E:failed",
                      "call:DL:r", "call:DL:failed", "call:n:s:ok", "call:n:s:failed"]
    alltimers = sorted({t for ts in timers.values() for t in ts})
    named = ["btn:a", "btn:b", "btn:c"]
    nodes = msgs + secs

    def gen_variant():
        v = {}
        if R.random() < 0.4:
            v["if"] = R.choice(["c1", "c2"])
        k = R.random()
        if k < 0.3:
            pass  # stay
        elif k < 0.6:
            v["to"] = R.choice(nodes)
        elif k < 0.75 and secs:
            v["to"] = R.choice(secs); v["resume"] = True
        elif k < 0.9:
            v["to"] = R.choice(nodes); v["back"] = True
        elif secs:
            v["to"] = R.choice(secs); v["resume"] = True; v["back"] = True
        do = []
        for a in ["p", "bg", "L", "E", "DL", "n:s"]:
            if R.random() < 0.2:
                do.append(a)
        if R.random() < 0.2:
            do.append("p")
        R.shuffle(do)
        if do:
            v["do"] = do
        return v

    def gen_on(own_timers):
        on = {}
        evs = named + outcome_events + ["timer:" + t for t in (own_timers or alltimers)]
        for e in R.sample(evs, min(len(evs), R.randint(0, 5))):
            on[e] = [gen_variant() for _ in range(R.randint(1, 2))]
        return on

    def build(name):
        if name in tree:
            node = {"initial": R.choice(tree[name]["kids"]), "children": {}}
            on = gen_on(None if alltimers else [])
            on = {e: v for e, v in on.items() if not e.startswith("timer:") or alltimers}
            if on:
                node["on"] = on
            for k in tree[name]["kids"]:
                node["children"][k] = build(k)
            return node
        node = {}
        on = gen_on(list(timers.get(name, {})) or [])
        on = {e: v for e, v in on.items() if not e.startswith("timer:") or e[6:] in timers.get(name, {})}
        if on:
            node["on"] = on
        if name in timers:
            node["timers"] = timers[name]
        return node

    d = {"figram": 1, "conditions": {"c1": "C1", "c2": "C2"}, "calls": calls, "bot": build("")}
    return d, msgs, secs, alltimers, named + outcome_events


def gen_requests(R, D, msgs, secs, timers, events, n):
    acts = ["p", "bg", "L", "E", "DL", "n:s", "gone"]
    reqs = []
    for i in range(n):
        k = R.random()
        seq = R.choice([0, 1, 2, 3, 5, 8, MAX - 1, MAX]) if R.random() < 0.1 else R.randint(0, 8)
        if k < 0.08:
            r = {"op": "start"}
            if R.random() < 0.5: r["seq"] = seq
            if R.random() < 0.7:
                r["event"] = R.choice([e for e in events if not e.startswith("call:")] + ["btn:zz"])
                if R.random() < 0.7: r["guards"] = {"c1": R.random() < 0.5, "c2": R.random() < 0.5}
            reqs.append(r); continue
        at = R.choice(msgs + secs + ["gone"] if R.random() < 0.2 else msgs)
        snap = {"at": at}
        if R.random() < 0.4: snap["back"] = R.choice(msgs + ["gone"])
        if secs and R.random() < 0.4:
            snap["resume"] = {s: R.choice(msgs + ["gone"]) for s in R.sample(secs, R.randint(1, len(secs)))}
        tm = {}
        for t in timers + ["zz"]:
            if R.random() < 0.3: tm[t] = R.randint(0, max(seq, 1))
        if tm: snap["timers"] = tm
        pend = []
        for _ in range(R.randint(0, 3)):
            pend.append({"call": R.choice(acts), "token": R.randint(0, max(seq, 1))})
        if pend and R.random() < 0.15: pend.append(dict(R.choice(pend)))
        if pend or R.random() < 0.05: snap["pending"] = pend
        if seq or R.random() < 0.5: snap["seq"] = seq
        if k < 0.13:
            reqs.append({"op": "restart", "snapshot": snap}); continue
        r = {"op": "step", "snapshot": snap}
        if timers and R.random() < float(os.environ.get("TIMER_BIAS", "0")):
            e = "timer:" + R.choice(timers)
        else:
            e = R.choice(events + ["timer:" + t for t in timers] + ["btn:zz"])
        r["event"] = e
        if e.startswith("timer:"):
            r["token"] = tm.get(e[6:], R.randint(0, 3)) if R.random() < 0.8 else R.randint(0, 3)
        elif e.startswith("call:"):
            c = e[5:].rsplit(":", 1)[0]
            ws = [p["token"] for p in pend if p["call"] == c]
            r["token"] = R.choice(ws) if ws and R.random() < 0.8 else R.randint(0, 3)
        g = R.random() + float(os.environ.get("GUARD_SHIFT", "0"))
        if g < 0.7:
            r["guards"] = {"c1": R.random() < 0.5, "c2": R.random() < 0.5}
        elif g < 0.85:
            r["guards"] = {R.choice(["c1", "c2"]): R.random() < 0.5}
        reqs.append(r)
    for i, r in enumerate(reqs):
        r["id"] = i
    return reqs


COV = {}
def bump(k): COV[k] = COV.get(k, 0) + 1
def cov(r, exp, D):
    op = r["op"]; bump("op:" + op)
    if "need" in exp: bump("need"); return
    if "code" in exp: bump("err:" + exp["code"]); return
    if exp.get("recovered"): bump("recovered")
    if op != "step": 
        if op == "start" and "event" in r: bump("start+event")
        return
    s0, s1 = r["snapshot"], exp["snapshot"]
    e = r["event"]
    moved = any("send" in a for a in exp["actions"])
    adv = s1.get("seq", 0) == s0.get("seq", 0) + 1
    if e.startswith("call:"):
        w = [p for p in s0.get("pending", [])]
        c = e[5:].rsplit(":",1)[0]
        acc = {"call": c, "token": r["token"]} in w
        bump("outcome:" + ("accepted" if acc else "ignored"))
        if acc and not adv: bump("outcome:accepted-no-arrow")
        if acc and adv and not moved: bump("outcome:accepted-stay")
        if acc and moved: bump("outcome:accepted-move")
    if e.startswith("timer:") and adv and not moved: bump("timer:stay-consumed")
    if adv and not moved and not exp.get("recovered"): bump("stay")
    if any(a.get("detached") for a in exp["actions"]): bump("detached-in-reply")
    if any("token" in a and "call" in a for a in exp["actions"]): bump("call-with-token")
    if len(s1.get("pending", [])) >= 2: bump("pending>=2")
    if s1.get("back") != s0.get("back"): bump("back-changed")

def normalize(r):
    if "code" in r:
        return {"id": r.get("id"), "code": r["code"]}
    return r


def main():
    seed, count = int(sys.argv[1]), int(sys.argv[2])
    nreq = int(sys.argv[3]) if len(sys.argv) > 3 else 60
    outdir = sys.argv[4] if len(sys.argv) > 4 else tempfile.mkdtemp()
    R = random.Random(seed)
    skipped = mism = total = 0
    for k in range(count):
        d, msgs, secs, timers, events = gen_doc(R)
        p = os.path.join(outdir, f"doc_{seed}_{k}.bot.json")
        json.dump(d, open(p, "w"))
        c = subprocess.run(["timeout", "20", f"{BIN}/figram", "check", p], capture_output=True, text=True)
        if c.returncode != 0:
            skipped += 1
            if skipped <= 3:
                print("SKIP", p, c.stdout.strip()[:200])
            os.remove(p)
            continue
        D = Doc(d)
        reqs = gen_requests(R, D, msgs, secs, timers, events, nreq)
        inp = "".join(json.dumps(r) + "\n" for r in reqs)
        e = subprocess.run(["timeout", "30", f"{BIN}/figram-engine", p], input=inp, capture_output=True, text=True)
        act = [json.loads(l) for l in e.stdout.splitlines()]
        bad_here = 0
        for r, a in zip(reqs, act):
            total += 1
            exp = reply(D, r)
            cov(r, exp, D)
            if normalize(a) != normalize(exp):
                mism += 1; bad_here += 1
                if mism <= 10:
                    print("MISMATCH doc", p)
                    print("  request :", json.dumps(r))
                    print("  expected:", json.dumps(exp))
                    print("  actual  :", json.dumps(a))
        if len(act) != len(reqs):
            print("COUNT MISMATCH", p, len(act), len(reqs)); mism += 1
        if not bad_here:
            os.remove(p)
    print("coverage:", json.dumps(dict(sorted(COV.items()))))
    print(f"docs {count} skipped {skipped} requests {total} mismatches {mism}")
    sys.exit(1 if mism else 0)


if __name__ == "__main__":
    main()

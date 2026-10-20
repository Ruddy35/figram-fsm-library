#!/usr/bin/env python3
"""Stateful protocol test driver for figram-engine (audit agent engine-timers).

Independent of the project's harness (conformance.py / protocol.py): it has its
own reference model of the designer-level semantics (Chart.step, written from
FORMAT.md/Chart.lean, NOT from the compiled machine), talks to a real
`figram-engine` process line by line and checks invariants after EVERY reply.

Modes
  ptdriver.py ENGINE walk  DOC [--mutate M] [--seed S] [--users N] [--steps K]
      random stateful walks; exit 1 on any invariant violation.
  ptdriver.py ENGINE mutants DOC [--seed S] [--users N] [--steps K]
      runs `walk` against every `--mutants` mutant, prints killed/survived;
      exit 0 iff every mutant was killed (driver went red on each).
  ptdriver.py ENGINE suite DOCDIR
      targeted scenarios (need protocol, timers, seq, document upgrade,
      error handling); prints one JSON record per scenario. Hard checks
      (documented behaviour) make the exit code 1; observations do not.

Invariants checked after every reply (walk):
  I1 one line, valid JSON object, same id
  I2 reply kind exclusive: result | need | error
  I3 snapshot satisfies snapshot.schema.json (re-implemented: strict keys/bounds)
  I4 ignored event => snapshot byte-identical and no actions; fired => seq+1,
     exactly one send and it is last and equals snapshot.at
  I5 action order: cancel* call* start* send   (HOST.md §2)
  I6 timers' = (timers - cancelled) + started; started tokens == new seq
  I7 host scheduler (built only from actions) == snapshot.timers
  I8 need: non-empty, no duplicates, none of them already supplied
  I9 reply == reference model (snapshot incl. key order, actions, recovered, need set)
  I10 determinism: identical request => byte-identical reply
  I11 guards_irrelevant: extra guards / flipped unreachable guards => identical reply
  I12 error reply carries no snapshot; the old snapshot keeps working
"""

import copy
import json
import os
import queue
import random
import resource
import subprocess
import sys
import threading
import time

MAXINT = 2 ** 53 - 1


# ----------------------------------------------------------------------------
# engine process
# ----------------------------------------------------------------------------

class EngineDied(Exception):
    pass


class Engine:
    def __init__(self, binary, doc, mutate=None, timeout=30.0, as_limit=int(os.environ.get("PT_AS_LIMIT", "6000000000"))):
        args = [binary] + (["--mutate", mutate] if mutate else []) + [doc]

        def limits():
            resource.setrlimit(resource.RLIMIT_AS, (as_limit, as_limit))
        self.p = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, preexec_fn=limits)
        self.q = queue.Queue()
        self.timeout = timeout
        self.next_id = 1
        self.t = threading.Thread(target=self._reader, daemon=True)
        self.t.start()

    def _reader(self):
        for raw in self.p.stdout:
            self.q.put(raw)
        self.q.put(None)

    def send_raw(self, data: bytes, expect_reply=True, timeout=None):
        try:
            self.p.stdin.write(data)
            self.p.stdin.flush()
        except BrokenPipeError:
            raise EngineDied("stdin closed (engine died)")
        if not expect_reply:
            return None
        try:
            raw = self.q.get(timeout=timeout or self.timeout)
        except queue.Empty:
            return b"<TIMEOUT>"
        if raw is None:
            raise EngineDied("stdout EOF (engine died)")
        return raw

    def call(self, obj):
        """Send a request object (id added if absent); return (raw_line, reply)."""
        obj = dict(obj)
        if "id" not in obj:
            obj["id"] = self.next_id
            self.next_id += 1
        line = json.dumps(obj, separators=(",", ":")).encode() + b"\n"
        raw = self.send_raw(line)
        return raw, obj["id"]

    def close(self, timeout=10):
        try:
            self.p.stdin.close()
        except Exception:
            pass
        try:
            rc = self.p.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.p.kill()
            rc = "TIMEOUT"
        err = self.p.stderr.read().decode(errors="replace")
        return rc, err


# ----------------------------------------------------------------------------
# reference model (designer level, Chart semantics)
# ----------------------------------------------------------------------------

class Ref:
    def __init__(self, doc):
        self.doc = doc
        self.parent = {}      # id -> parent id (root "" -> None)
        self.initial = {}     # section id -> initial child
        self.kind = {}        # id -> "msg" | "sec"
        self.order = []       # preorder of node ids
        self.trans = {}       # (src, event) -> list of branches (guard, target, resume, calls)
        self.timers = []      # (name, node, ms) preorder
        self.events = []
        self._flat("", None, doc["bot"])
        self.leaves = [n for n in self.order if self.kind[n] == "msg"]
        self.hist_secs = {b[1] for bs in self.trans.values() for b in bs if b[2]}
        self.conditions = list(doc.get("conditions", {}).keys())

    def _flat(self, nid, parent, node):
        self.order.append(nid)
        self.parent[nid] = parent
        if "children" in node:
            self.kind[nid] = "sec"
            self.initial[nid] = node["initial"]
        else:
            self.kind[nid] = "msg"
        for ev, vs in node.get("on", {}).items():
            self.trans[(nid, ev)] = [(v.get("if"), v["to"], bool(v.get("resume", False)),
                                      list(v.get("do", []))) for v in vs]
            if ev not in self.events:
                self.events.append(ev)
        if "children" not in node:
            for tname, ms in node.get("timers", {}).items():
                self.timers.append((tname, nid, ms))
        for k, c in node.get("children", {}).items():
            self._flat(k, nid, c)

    # structure
    def anc(self, n):
        out = [n]
        seen = 0
        while self.parent.get(n) is not None and seen <= len(self.order):
            n = self.parent[n]
            out.append(n)
            seen += 1
        return out

    def enter(self, n):
        k = 0
        while self.kind.get(n) == "sec" and k <= len(self.order):
            n = self.initial[n]
            k += 1
        return n

    def hist_cands(self, s):
        return [l for l in self.leaves if s in self.anc(l) and l != s]

    def timers_of(self, n):
        return [t for t in self.timers if t[1] == n]

    def entry(self, T):
        acts = [("remember", s, T) for s in self.anc(T) if s in self.hist_secs and s != T]
        acts += [("start", t[0], t[2]) for t in self.timers_of(T)]
        acts.append(("send", T))
        return acts

    def exit(self, L):
        return [("cancel", t[0]) for t in self.timers_of(L)]

    def cands(self, L, ev):
        out = []
        for a in self.anc(L):
            out += self.trans.get((a, ev), [])
        return out

    def resolve(self, snap, b):
        _, to, resume, _ = b
        if not resume:
            return self.enter(to)
        v = dict(snap["resume"]).get(to)
        # lookup = first entry
        for k, val in snap["resume"]:
            if k == to:
                v = val
                break
        if v is not None and v in self.hist_cands(to):
            return v
        return self.enter(to)

    # snapshot ops (snapshot = dict at, resume[list of pairs], timers[list], seq)
    @staticmethod
    def realize(s, T, acts):
        tok = s["seq"] + 1
        n = {"at": T, "resume": list(s["resume"]), "timers": list(s["timers"]), "seq": tok}
        out = []
        for a in acts:
            if a[0] == "remember":
                n["resume"] = [(a[1], a[2])] + [p for p in n["resume"] if p[0] != a[1]]
            elif a[0] == "start":
                n["timers"] = [(a[1], tok)] + [p for p in n["timers"] if p[0] != a[1]]
                out.append({"start_timer": a[1], "ms": a[2], "token": tok})
            elif a[0] == "cancel":
                n["timers"] = [p for p in n["timers"] if p[0] != a[1]]
                out.append({"cancel_timer": a[1]})
            elif a[0] == "call":
                out.append({"call": a[1]})
            elif a[0] == "send":
                out.append({"send": a[1]})
        return n, out

    def init_acts(self):
        return self.entry(self.enter(""))

    def start(self):
        return self.realize({"at": "", "resume": [], "timers": [], "seq": 0},
                            self.enter(""), self.init_acts())

    def recover(self, s):
        return self.realize(s, self.enter(""), [("cancel", p[0]) for p in s["timers"]] + self.init_acts())

    def restart(self, s):
        return self.recover(dict(s, resume=[]))

    @staticmethod
    def accepts(s, ev, tok):
        if not ev.startswith("timer:"):
            return True
        for k, v in s["timers"]:
            if k == ev[6:]:
                return v == tok
        return False

    def required(self, s, ev, tok):
        """dedup(guards of candidate branches) keeping LAST occurrences (Compile.dedup)."""
        if s["at"] not in self.leaves or not self.accepts(s, ev, tok):
            return []
        gs = [b[0] for b in self.cands(s["at"], ev) if b[0] is not None]
        out = []
        for i, g in enumerate(gs):
            if g not in gs[i + 1:]:
                out.append(g)
        return out

    def needed(self, s, ev, tok, known):
        """Guards `need` asks for (P-14, FORMAT §7.3): candidate variants in order up to the
        first unconditional one or the first guard known to be true; guards not supplied on
        the way, each once."""
        if s["at"] not in self.leaves or not self.accepts(s, ev, tok):
            return []
        out = []
        for b in self.cands(s["at"], ev):
            g = b[0]
            if g is None:
                break
            if g in known:
                if known[g]:
                    break
                continue
            if g not in out:
                out.append(g)
        return out

    def unreachable_only(self, s, ev):
        """Guards that only appear after the first unconditional candidate branch."""
        cs = self.cands(s["at"], ev)
        idx = next((i for i, b in enumerate(cs) if b[0] is None), None)
        if idx is None:
            return []
        before = {b[0] for b in cs[:idx] if b[0]}
        return sorted({b[0] for b in cs[idx + 1:] if b[0] and b[0] not in before})

    def step(self, s, ev, tok, env):
        """-> (snapshot, actions, recovered)"""
        if s["at"] not in self.leaves:
            n, a = self.recover(s)
            return n, a, True
        if not self.accepts(s, ev, tok):
            return s, [], False
        for b in self.cands(s["at"], ev):
            if b[0] is None or env.get(b[0], False):
                T = self.resolve(s, b)
                acts = self.exit(s["at"]) + [("call", c) for c in b[3]] + self.entry(T)
                n, a = self.realize(s, T, acts)
                return n, a, False
        return s, [], False


def snap_to_json(s):
    o = {"at": s["at"]}
    if s["resume"]:
        o["resume"] = {k: v for k, v in s["resume"]}
    if s["timers"]:
        o["timers"] = {k: v for k, v in s["timers"]}
    if s["seq"] != 0:
        o["seq"] = s["seq"]
    return o


def snap_from_json(o):
    return {"at": o["at"], "resume": list(o.get("resume", {}).items()),
            "timers": list(o.get("timers", {}).items()), "seq": o.get("seq", 0)}


# ----------------------------------------------------------------------------
# invariant checking
# ----------------------------------------------------------------------------

class Checker:
    def __init__(self, verbose=False):
        self.violations = []
        self.checks = 0
        self.per = {}
        self.verbose = verbose

    def ok(self, cond, inv, detail):
        self.checks += 1
        self.per[inv] = self.per.get(inv, 0) + 1
        if not cond:
            self.violations.append((inv, detail))
            if self.verbose:
                print(f"VIOLATION {inv}: {detail}", file=sys.stderr)
        return cond


def schema_ok(o):
    if not isinstance(o, dict) or "at" not in o:
        return False
    if set(o) - {"v", "at", "resume", "timers", "seq"}:
        return False
    if "v" in o and o["v"] != 1:
        return False
    if not isinstance(o["at"], str) or not o["at"]:
        return False
    for k, v in o.get("resume", {}).items():
        if not k or not isinstance(v, str) or not v:
            return False
    for k, v in o.get("timers", {}).items():
        if not k or type(v) is not int or not 0 <= v <= MAXINT:
            return False
    if "seq" in o and (type(o["seq"]) is not int or not 0 <= o["seq"] <= MAXINT):
        return False
    return True


def parse_reply(ck, raw, rid, where):
    ok = ck.ok(raw.endswith(b"\n") and raw.count(b"\n") == 1, "I1", f"{where}: not one line: {raw[:200]!r}")
    try:
        r = json.loads(raw)
    except Exception:
        ck.ok(False, "I1", f"{where}: invalid JSON {raw[:200]!r}")
        return None
    if not ck.ok(isinstance(r, dict) and r.get("id") == rid and type(r.get("id")) == type(rid),
                 "I1", f"{where}: id mismatch {raw[:200]!r} expected {rid!r}"):
        return r if isinstance(r, dict) else None
    kinds = [k for k in ("snapshot", "need", "error", "protocol") if k in r]
    ck.ok(len(kinds) == 1, "I2", f"{where}: reply kinds {kinds}: {raw[:200]!r}")
    if "snapshot" in r:
        ck.ok(set(r) <= {"id", "snapshot", "actions", "recovered"} and "actions" in r
              and r.get("recovered", True) is True, "I2", f"{where}: bad result shape {raw[:200]!r}")
        ck.ok(schema_ok(r["snapshot"]), "I3", f"{where}: snapshot violates schema {r['snapshot']}")
    return r


def check_actions_shape(ck, before, after, actions, where, step_kind):
    """I4..I6 (model-free)."""
    if not actions:
        ck.ok(snap_to_json(before) == after, "I4", f"{where}: no actions but snapshot changed {after}")
        return
    ck.ok(after.get("seq", 0) == before["seq"] + 1, "I4", f"{where}: seq {before['seq']} -> {after.get('seq')}")
    sends = [a for a in actions if "send" in a]
    ck.ok(len(sends) == 1 and "send" in actions[-1] and actions[-1]["send"] == after["at"],
          "I4", f"{where}: send rule broken {actions}")
    rank = {"cancel_timer": 0, "call": 1, "start_timer": 2, "send": 3}
    ranks = [rank[next(iter(a))] for a in actions]
    ck.ok(ranks == sorted(ranks), "I5", f"{where}: action order {actions}")
    t = dict(before["timers"])
    for a in actions:
        if "cancel_timer" in a:
            t.pop(a["cancel_timer"], None)
        if "start_timer" in a:
            ck.ok(a["token"] == after.get("seq"), "I6", f"{where}: token {a['token']} != seq {after.get('seq')}")
            t[a["start_timer"]] = a["token"]
    ck.ok(t == after.get("timers", {}), "I6", f"{where}: timers {after.get('timers', {})} expected {t}")


# ----------------------------------------------------------------------------
# random stateful walk
# ----------------------------------------------------------------------------

class User:
    def __init__(self, name):
        self.name = name
        self.snap = None
        self.sched = {}         # host scheduler: timer name -> token (from actions only)
        self.fired = []         # (name, token) ever scheduled — for replays / stale fires
        self.keys = {}          # idempotency key user:seq:idx -> action


def apply_host(u, snap_json, actions):
    for a in actions:
        if "cancel_timer" in a:
            u.sched.pop(a["cancel_timer"], None)
        if "start_timer" in a:
            u.sched[a["start_timer"]] = a["token"]
            u.fired.append((a["start_timer"], a["token"]))
    seq = snap_json.get("seq", 0)
    for i, a in enumerate(actions):
        u.keys[f"{u.name}:{seq}:{i}"] = a
    u.snap = snap_from_json(snap_json)


class Session:
    """One engine process + checker; checked operations on simulated users."""

    def __init__(self, binary, docpath, mutate=None, seed=1, verbose=False):
        self.doc = json.load(open(docpath))
        self.ref = Ref(self.doc)
        self.ck = Checker(verbose)
        self.rnd = random.Random(seed)
        self.seed = seed
        self.eng = Engine(binary, docpath, mutate)
        self.replies = 0
        self.nuser = 0

    def call(self, req, where):
        raw, rid = self.eng.call(req)
        self.replies += 1
        return raw, rid, parse_reply(self.ck, raw, rid, where)

    def hello(self):
        _, _, r = self.call({"op": "hello"}, "hello")
        self.ck.ok(r is not None and r.get("states") == len(self.ref.leaves) and r.get("protocol") == 1,
                   "I9", f"hello {r}")

    def new_user(self):
        ck, ref = self.ck, self.ref
        u = User(f"u{self.nuser}")
        self.nuser += 1
        _, _, r = self.call({"op": "start"}, "start")
        es, ea = ref.start()
        ck.ok(r is not None and r.get("snapshot") == snap_to_json(es) and r.get("actions") == ea
              and "recovered" not in r, "I9", f"start {r} expected {snap_to_json(es)} {ea}")
        if r is None or "snapshot" not in r:
            return None
        ck.ok(list(r["snapshot"].get("timers", {}).items()) == es["timers"], "I9", "start timer order")
        apply_host(u, r["snapshot"], r["actions"])
        return u

    def bad_request(self, u, where):
        """I12: malformed request => error, no snapshot; caller keeps the old snapshot."""
        s = snap_to_json(u.snap)
        bad = self.rnd.choice([
            {"op": "step", "snapshot": s},
            {"op": "step", "snapshot": s, "event": "btn:x", "guards": {"g": 1}},
            {"op": "step", "snapshot": dict(s, extra=1), "event": "btn:x"},
            {"op": "step", "snapshot": s, "event": "timer:x"},
            {"op": "step", "snapshot": s, "event": "timer:x", "token": -1},
            {"op": "step", "snapshot": s, "event": "timer:x", "token": 1.5},
            {"op": "step", "snapshot": dict(s, seq=MAXINT + 1), "event": "btn:x"},
            {"op": "nope"},
            {"op": "restart"},
        ])
        _, _, r = self.call(bad, where + " bad")
        self.ck.ok(r is not None and "error" in r and "snapshot" not in r, "I12",
                   f"{where}: bad request {bad} -> {r}")

    def restart(self, u, where):
        ck, ref = self.ck, self.ref
        _, _, r = self.call({"op": "restart", "snapshot": snap_to_json(u.snap)}, where + " restart")
        es, ea = ref.restart(u.snap)
        ck.ok(r is not None and r.get("snapshot") == snap_to_json(es) and r.get("actions") == ea
              and "recovered" not in r, "I9", f"{where}: restart {r} expected {snap_to_json(es)} {ea}")
        if r and "snapshot" in r:
            ck.ok(r["snapshot"].get("seq") == u.snap["seq"] + 1, "I4", f"{where}: restart seq")
            apply_host(u, r["snapshot"], r["actions"])
            ck.ok(u.sched == r["snapshot"].get("timers", {}), "I7", f"{where}: sched {u.sched} vs {r['snapshot']}")

    def step(self, u, ev, tok, where, full=None):
        """Checked step incl. the need round-trip; `full` fixes final guard values."""
        ck, ref, rnd = self.ck, self.ref, self.rnd
        base = {"op": "step", "snapshot": snap_to_json(u.snap), "event": ev}
        if tok is not None:
            base["token"] = tok
        req_g = ref.required(u.snap, ev, tok)
        if full is None:
            full = {g: rnd.random() < 0.5 for g in req_g}
        partial = {g: full.get(g, False) for g in req_g if rnd.random() < 0.5}
        req = dict(base)
        if partial or rnd.random() < 0.5:
            req["guards"] = partial
        raw, rid, r = self.call(req, where)
        if r is None:
            return None
        exp_need = ref.needed(u.snap, ev, tok, partial)
        if exp_need:
            ck.ok("need" in r and sorted(r["need"]) == sorted(exp_need), "I9",
                  f"{where}: ev={ev} tok={tok} guards={partial} reply={r} expected need {exp_need}")
            if "need" in r:
                ck.ok(len(r["need"]) == len(set(r["need"])) and r["need"]
                      and not set(r["need"]) & set(partial), "I8", f"{where}: need {r['need']}")
            g2 = dict(partial)
            for g in (r.get("need") or exp_need):
                g2[g] = full.get(g, False)
            req = dict(base, guards=g2)
            full = g2
            raw, rid, r = self.call(req, where + " retry")
            if r is None:
                return None
        else:
            full = partial
            ck.ok("need" not in r, "I9", f"{where}: unexpected need {r} ev={ev} tok={tok}")
        if "error" in r:
            ck.ok(False, "I9", f"{where}: unexpected error {r} for {req}")
            return None
        if "snapshot" not in r:
            ck.ok(False, "I9", f"{where}: still no result {r}")
            return None
        es, ea, erec = ref.step(u.snap, ev, tok, full)
        ck.ok(r["snapshot"] == snap_to_json(es) and r["actions"] == ea and r.get("recovered", False) == erec,
              "I9", f"{where}: ev={ev} tok={tok} guards={full}\n   got {r}\n   exp {snap_to_json(es)} {ea} rec={erec}")
        ck.ok(list(r["snapshot"].get("resume", {}).items()) == es["resume"]
              and list(r["snapshot"].get("timers", {}).items()) == es["timers"],
              "I9", f"{where}: key order differs {r['snapshot']} vs {es}")
        check_actions_shape(ck, u.snap, r["snapshot"], r["actions"], where, "step")
        if rnd.random() < 0.15:                                   # I10
            raw2, _ = self.eng.call(dict(req, id=rid))
            self.replies += 1
            ck.ok(raw2 == raw, "I10", f"{where}: nondeterministic {raw!r} vs {raw2!r}")
        if rnd.random() < 0.25:                                   # I11
            extra = dict(full)
            for g in ref.conditions + ["undeclared_guard"]:
                if g not in req_g:
                    extra[g] = rnd.random() < 0.5
            for g in ref.unreachable_only(u.snap, ev):
                extra[g] = not extra.get(g, False)
            raw3, _ = self.eng.call(dict(req, id=rid, guards=extra))
            self.replies += 1
            ck.ok(raw3 == raw, "I11", f"{where}: guards changed result {raw!r} vs {raw3!r} ({extra})")
        apply_host(u, r["snapshot"], r["actions"])
        ck.ok(u.sched == r["snapshot"].get("timers", {}), "I7",
              f"{where}: host scheduler {u.sched} vs snapshot {r['snapshot'].get('timers', {})}")
        return r

    # -- random walk ---------------------------------------------------------
    def random_walk(self, users, steps):
        ref, rnd = self.ref, self.rnd
        for _ in range(users):
            u = self.new_user()
            if u is None:
                continue
            for k in range(steps):
                where = f"walk seed={self.seed} user={u.name} step={k} at={u.snap['at']}"
                roll = rnd.random()
                if roll < 0.04:
                    self.bad_request(u, where)
                    continue
                if roll < 0.07:
                    self.restart(u, where)
                    continue
                tok = None
                own = [e for e in ref.events if ref.trans.get((u.snap["at"], e)) and not e.startswith("timer:")]
                local = [e for e in ref.events if ref.cands(u.snap["at"], e) and not e.startswith("timer:")]
                c = rnd.random()
                if c < 0.25 and u.sched:
                    name = rnd.choice(sorted(u.sched))
                    ev, tok = "timer:" + name, u.sched[name]          # timer expires
                elif c < 0.35 and u.fired:
                    name, tok = rnd.choice(u.fired)                    # replay / stale / in-flight
                    ev = "timer:" + name
                elif c < 0.40:
                    ev = "timer:" + rnd.choice(["", "zzz"] + [t[0] for t in ref.timers])
                    tok = rnd.choice([0, u.snap["seq"], u.snap["seq"] + 1, MAXINT + 2, 2 ** 64])
                elif c < 0.75 and own:
                    ev = rnd.choice(own)
                elif c < 0.92 and local:
                    ev = rnd.choice(local)
                else:
                    ev = rnd.choice(ref.events + ["btn:none", "msg:other"])
                    if ev.startswith("timer:"):
                        tok = rnd.choice([u.snap["seq"], 1])
                self.step(u, ev, tok, where)

    # -- model-guided coverage: every reachable (message, event, winning branch) --
    def coverage_targets(self, max_depth=14):
        """BFS in the model; returns {(at, ev, branch_index): path}. A path is a list of
        (event, use_current_token, guards)."""
        ref = self.ref
        start, _ = ref.start()
        key = lambda s: (s["at"], tuple(sorted(s["resume"])), tuple(sorted(n for n, _ in s["timers"])))
        seen = {key(start)}
        frontier = [(start, [])]
        targets = {}
        named = [e for e in ref.events if not e.startswith("timer:")]
        for _depth in range(max_depth):
            nxt = []
            for s, path in frontier:
                evs = [(e, None) for e in named] + [("timer:" + n, t) for n, t in s["timers"]]
                for ev, tok in evs:
                    cs = ref.cands(s["at"], ev)
                    if not cs:
                        continue
                    gnames = sorted({b[0] for b in cs if b[0]})
                    for bits in range(2 ** len(gnames)):
                        env = {g: bool(bits >> i & 1) for i, g in enumerate(gnames)}
                        win = next((i for i, b in enumerate(cs) if b[0] is None or env.get(b[0])), None)
                        step = (ev, tok is not None, env)
                        if win is None:
                            targets.setdefault((s["at"], ev, -1), path + [step])   # nothing wins: ignored
                            continue
                        targets.setdefault((s["at"], ev, win), path + [step])
                        n, _a, _r = ref.step(s, ev, tok, env)
                        if key(n) not in seen:
                            seen.add(key(n))
                            nxt.append((n, path + [step]))
            frontier = nxt
        return targets

    def coverage(self):
        targets = self.coverage_targets()
        for (at, ev, win), path in sorted(targets.items()):
            u = self.new_user()
            if u is None:
                return len(targets)
            for i, (e, use_tok, env) in enumerate(path):
                tok = u.sched.get(e[6:]) if use_tok else None
                if use_tok and tok is None:
                    self.ck.ok(False, "I7", f"cover {at}/{ev}/{win}: timer {e} not scheduled by host")
                    break
                r = self.step(u, e, tok, f"cover target={at}/{ev}/{win} i={i}", full=dict(env))
                if r is None:
                    break
                # after each step: stale replay of every token ever issued must be ignored unless current
                stale = [(n, t) for n, t in u.fired if u.sched.get(n) != t]
                if self.rnd.random() < 0.3 and stale:
                    name, t = self.rnd.choice(stale)
                    r2 = self.step(u, "timer:" + name, t, f"cover-replay target={at}/{ev}/{win} i={i}")
                    self.ck.ok(r2 is not None and r2["actions"] == [], "T1",
                               f"stale/replayed timer {name}:{t} was not ignored: {r2}")
        return len(targets)

    def close(self):
        rc, err = self.eng.close()
        self.ck.ok(rc == 0, "I1", f"engine exit code {rc} stderr={err[:300]}")


def branch_index_map(ref):
    """k -> (message, event, index in candidates), in the order of Machine.rows
    (Compile.lean: leafIds.flatMap (events.map compileRow), events = dedup keeping LAST)."""
    evs_all = [e for (_src, e) in ref.trans.keys()]
    events = [e for i, e in enumerate(evs_all) if e not in evs_all[i + 1:]]
    out = []
    for L in ref.leaves:
        for e in events:
            for i, _b in enumerate(ref.cands(L, e)):
                out.append((L, e, i))
    return out


def row_index_map(ref):
    evs_all = [e for (_src, e) in ref.trans.keys()]
    events = [e for i, e in enumerate(evs_all) if e not in evs_all[i + 1:]]
    return [(L, e) for L in ref.leaves for e in events if ref.cands(L, e)]


def swap_equivalent(ref, L, e):
    """Reversing branches is unobservable iff the winning branch's (target, resume, calls)
    is the same for every guard assignment."""
    cs = ref.cands(L, e)
    gn = sorted({b[0] for b in cs if b[0]})
    for bits in range(2 ** len(gn)):
        env = {g: bool(bits >> i & 1) for i, g in enumerate(gn)}
        f = next((b[1:] for b in cs if b[0] is None or env.get(b[0])), None)
        r = next((b[1:] for b in reversed(cs) if b[0] is None or env.get(b[0])), None)
        if f != r:
            return False
    return True


def walk(binary, docpath, mutate=None, seed=1, users=20, steps=40, verbose=False):
    s = Session(binary, docpath, mutate, seed, verbose)
    ntargets = 0
    try:
        s.hello()
        ntargets = s.coverage()
        s.random_walk(users, steps)
    except EngineDied as e:
        s.ck.ok(False, "I1", f"engine died: {e}")
    s.close()
    s.ck.targets = ntargets
    return s.ck, s.replies


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------

def main(argv):
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("engine")
    ap.add_argument("mode", choices=["walk", "mutants", "suite"])
    ap.add_argument("doc")
    ap.add_argument("--mutate")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--users", type=int, default=20)
    ap.add_argument("--steps", type=int, default=40)
    ap.add_argument("-v", action="store_true")
    a = ap.parse_args(argv)
    if a.mode == "walk":
        ck, n = walk(a.engine, a.doc, a.mutate, a.seed, a.users, a.steps, a.v)
        print(json.dumps({"doc": a.doc, "mutate": a.mutate, "seed": a.seed, "replies": n,
                          "coverage_targets": ck.targets,
                          "checks": ck.checks, "per_invariant": dict(sorted(ck.per.items())),
                          "violations": len(ck.violations)}))
        for inv, d in ck.violations[:5]:
            print(f"  {inv}: {d}")
        return 1 if ck.violations or n == 0 or ck.checks == 0 else 0
    if a.mode == "mutants":
        ms = subprocess.run([a.engine, "--mutants", a.doc], capture_output=True, text=True, timeout=30)
        names = ms.stdout.split()
        survived, equivalents = [], []
        sess = Session(a.engine, a.doc)
        reach = set(sess.coverage_targets().keys())
        sess.eng.close()
        bmap = branch_index_map(sess.ref)
        for m in names:
            ck, n = walk(a.engine, a.doc, m, a.seed, a.users, a.steps)
            killed = bool(ck.violations)
            parts = m.split(":")
            equiv = None
            if parts[0] in ("redirect", "drop-guard") and parts[1].isdigit() and int(parts[1]) < len(bmap):
                equiv = bmap[int(parts[1])] not in reach
            if parts[0] == "swap-branches" and parts[1].isdigit():
                rows = row_index_map(sess.ref)
                if int(parts[1]) < len(rows) and swap_equivalent(sess.ref, *rows[int(parts[1])]):
                    equiv = rows[int(parts[1])]
                    if not killed:
                        equivalents.append(m)
                        print(json.dumps({"mutant": m, "killed": False, "equivalent_same_outcome_row":
                                          list(equiv)}, ensure_ascii=False))
                        continue
            if not killed and equiv:
                equivalents.append(m)
                print(json.dumps({"mutant": m, "killed": False, "equivalent_unreachable_branch":
                                  list(bmap[int(parts[1])])}, ensure_ascii=False))
                continue
            first = ck.violations[0] if ck.violations else ("", "")
            print(json.dumps({"mutant": m, "killed": killed, "replies": n, "checks": ck.checks,
                              "violations": len(ck.violations), "first": f"{first[0]}: {first[1][:300]}"},
                             ensure_ascii=False))
            if not killed:
                survived.append(m)
        print(json.dumps({"doc": a.doc, "mutants": len(names), "killed": len(names) - len(survived) - len(equivalents),
                          "equivalent": equivalents, "survived": survived}))
        return 0 if names and not survived else 1
    if a.mode == "suite":
        import ptsuite
        return ptsuite.run(a.engine, a.doc)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

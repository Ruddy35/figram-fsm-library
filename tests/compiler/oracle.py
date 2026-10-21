"""Independent reference interpreter of Figram designer semantics.

Written from the PROSE of docs/format.md (sections 3-4, the decisions of the
specification 1-8 in section 7, section 8) and docs/host-contract.md -- NOT
from Figram/Chart.lean.  Wherever the prose is ambiguous the interpreter has
an explicit switch (see `Options`); the defaults are the readings that the
Lean implementation turned out to use, the alternatives are what a literal
reading of the prose (or the designer-level model "a reminder lives on its
message") would give.  The differential driver counts how often each
ambiguity is *hit* (the two readings would give different observable results).

Document model (docs/format.md, section 4):
  section  <=> object has "children"; "initial" is a direct child (V2)
  message  has optional "timers": {name: ms}
  "on": {event: [variant]}, variant = {"if"?, "to", "resume"?, "do"?}
Semantics (docs/format.md, section 7):
  * entering a section = entering its initial node, recursively, until a
    message is reached ("an arrow into a section leads to its initial message");
  * candidates for event e at message M: M's own variants, then those of the
    section above, ... up to the bot root (decision 1); the first variant
    whose condition holds wins;
  * transition = exit(M) [cancel M's timers] -> arrow "do" calls ->
    entry(T) [remember T in every enclosing section that is the target of
    some resume arrow (decision 4); start T's timers; send T] (decision 2);
  * self arrow re-sends and restarts timers (decision 3);
  * resume arrow into S goes to the message remembered for S; if that
    message is gone -> S's initial message (decision 5);
  * no matching variant -> ignore (decision 6);
  * timer event accepted only with the token of its current launch (7);
  * snapshot pointing at a non-existent message -> start over: cancel all
    snapshot timers, keep remembered places, "recovered": true (8; format.md §8).
"""

import json

MAX_INT = 2 ** 53 - 1
FORMAT_DOC_KEYS = ["figram", "requires", "conditions", "calls", "variables", "bot"]
FORMAT_NODE_KEYS = ["initial", "children", "on", "timers"]
FORMAT_VARIANT_KEYS = ["if", "to", "resume", "do"]
KNOWN_FEATURES = []


class DocError(Exception):
    def __init__(self, errors):
        super().__init__("; ".join(errors))
        self.errors = errors


class RequestError(Exception):
    pass


class Options:
    """Ambiguity switches. Defaults = how Lean resolved them (verified by the
    directed tests in directed.py)."""

    def __init__(self, **kw):
        # A1: remembered message still exists but is no longer inside the
        #     section.  'initial' (Lean) | 'follow' (literal decision 5: only
        #     "no longer in the document" falls back)
        self.moved_memory = kw.pop("moved_memory", "initial")
        # A3: which guards a step "needs": checked by bounds in the driver,
        #     this flag only selects what `need()` returns as the oracle's own
        #     answer: 'all' (every guard on the bubbling chain) | 'min'.
        self.need = kw.pop("need", "all")
        # A7: timer event acceptance. 'token' (Lean: snapshot token matches) |
        #     'owned' (designer model: the CURRENT message must own the timer)
        self.timer_accept = kw.pop("timer_accept", "token")
        # A7b: what exit cancels. 'doc' (timers the current message owns in
        #     the current document) | 'snapshot' (every timer active in the
        #     snapshot, i.e. "the timers of the place being left")
        self.exit_cancels = kw.pop("exit_cancels", "doc")
        # Deliberate bugs for red-proofing the differential driver.
        self.bug = kw.pop("bug", None)
        if kw:
            raise TypeError(f"unknown options {kw}")


def _pairs(pairs):
    keys = [k for k, _ in pairs]
    if len(set(keys)) != len(keys):
        raise DocError(["duplicate key in object"])
    return pairs  # keep ordered list of pairs


def parse_json(text):
    return json.loads(text, object_pairs_hook=_pairs)


def get(pairs, k, default=None):
    for kk, v in pairs:
        if kk == k:
            return v
    return default


def has(pairs, k):
    return any(kk == k for kk, _ in pairs)


def is_obj(x):
    return isinstance(x, list) and all(isinstance(p, tuple) and len(p) == 2 for p in x)


def as_int(x):
    """Format integer field: value-based (7200000, 7.2e6, 7200000.0 equal)."""
    if isinstance(x, bool):
        return None
    if isinstance(x, int):
        return x if 0 <= x <= MAX_INT else None
    if isinstance(x, float):
        if x.is_integer() and 0 <= x <= MAX_INT:
            return int(x)
    return None


class Node:
    __slots__ = ("name", "parent", "is_section", "initial", "on", "timers")

    def __init__(self, name, parent, is_section, initial, on, timers):
        self.name, self.parent, self.is_section = name, parent, is_section
        self.initial, self.on, self.timers = initial, on, timers


class Variant:
    __slots__ = ("cond", "to", "resume", "calls")

    def __init__(self, cond, to, resume, calls):
        self.cond, self.to, self.resume, self.calls = cond, to, resume, calls


class Bot:
    """A checked document plus the step semantics."""

    ROOT = ""  # the bot root section has no name of its own

    def __init__(self, doc, opts=None):
        self.opts = opts or Options()
        if isinstance(doc, (str, bytes)):
            doc = parse_json(doc)
        self.nodes = {}
        self.conditions, self.calls = [], []
        errs = []
        self._load(doc, errs)
        if errs:
            raise DocError(errs)
        self.history = {v.to for n in self.nodes.values() for _, vs in n.on for v in vs if v.resume}

    # ------------------------------------------------------------ checking
    def _strmap(self, doc, key, errs):
        m = get(doc, key, [])
        if not is_obj(m):
            errs.append(f"{key}: not an object")
            return []
        out = []
        for k, v in m:
            if not isinstance(v, str):
                errs.append(f"{key}.{k}: not a string")
            if k == "":
                errs.append(f"{key}: empty name")
            out.append(k)
        return out

    def _load(self, doc, errs):
        if not is_obj(doc):
            raise DocError(["document must be an object"])
        fig = get(doc, "figram")
        if as_int(fig) != 1:
            raise DocError(["figram must be 1"])
        req = get(doc, "requires", [])
        if not isinstance(req, list) or not all(isinstance(r, str) for r in req):
            raise DocError(["requires must be a list of strings"])
        if any(r not in KNOWN_FEATURES for r in req):
            raise DocError(["unknown required feature"])
        self.conditions = self._strmap(doc, "conditions", errs)
        self.calls = self._strmap(doc, "calls", errs)
        self._strmap(doc, "variables", errs)
        bot = get(doc, "bot")
        if bot is None:
            raise DocError(["no bot"])
        if not is_obj(bot) or not has(bot, "children"):
            raise DocError(["bot must be a section"])
        self._node(self.ROOT, None, bot, errs, top=True)
        # V2: initial is a direct child; V3/V4/V5 references
        timer_names = {t for n in self.nodes.values() for t, _ in n.timers}
        for n in self.nodes.values():
            if n.is_section:
                ini = self.nodes.get(n.initial)
                if ini is None or ini.parent != n.name:
                    errs.append(f"initial {n.initial!r} of {n.name!r} is not its child")
            for ev, vs in n.on:
                if ev == "":
                    errs.append("empty event name")
                if ev.startswith("timer:") and ev[6:] not in timer_names:
                    errs.append(f"event {ev!r}: no such timer")
                for v in vs:
                    tgt = self.nodes.get(v.to)
                    if v.to == "" or tgt is None:
                        errs.append(f"target {v.to!r} does not exist")
                    elif v.resume and not tgt.is_section:
                        errs.append(f"resume target {v.to!r} is not a section")
                    if v.cond is not None and (v.cond == "" or v.cond not in self.conditions):
                        errs.append(f"condition {v.cond!r} undeclared")
                    for c in v.calls:
                        if c == "" or c not in self.calls:
                            errs.append(f"call {c!r} undeclared")

    def _node(self, name, parent, obj, errs, top=False):
        if not is_obj(obj):
            raise DocError([f"node {name!r} must be an object"])
        if not top:
            if name == "":
                errs.append("empty node name")
            if name in self.nodes:
                errs.append(f"node {name!r} declared twice")  # V1
        on = []
        onj = get(obj, "on", [])
        if not is_obj(onj):
            raise DocError([f"{name}: on must be an object"])
        for ev, arr in onj:
            if not isinstance(arr, list):
                raise DocError([f"{name}.{ev}: variants must be a list"])
            vs = []
            for vj in arr:
                if not is_obj(vj):
                    raise DocError(["variant must be an object"])
                cond = get(vj, "if")
                if cond is not None and not isinstance(cond, str):
                    raise DocError(["if: string expected"])
                if has(vj, "if") and cond is None:
                    raise DocError(["if: null"])
                to = get(vj, "to")
                if not isinstance(to, str):
                    raise DocError(["variant needs to"])
                res = get(vj, "resume", False)
                if not isinstance(res, bool):
                    raise DocError(["resume: bool expected"])
                do = get(vj, "do", [])
                if not isinstance(do, list) or not all(isinstance(c, str) for c in do):
                    raise DocError(["do: list of strings expected"])
                vs.append(Variant(cond, to, res, list(do)))
            on.append((ev, vs))
        if has(obj, "children"):
            if has(obj, "timers"):
                raise DocError(["section cannot have timers"])
            ini = get(obj, "initial")
            if not isinstance(ini, str):
                raise DocError(["section needs initial"])
            ch = get(obj, "children")
            if not is_obj(ch):
                raise DocError(["children must be an object"])
            node = Node(name, parent, True, ini, on, [])
            if name not in self.nodes:
                self.nodes[name] = node
            for k, v in ch:
                self._node(k, name, v, errs)
        else:
            if has(obj, "initial"):
                raise DocError(["initial without children"])
            tj = get(obj, "timers", [])
            if not is_obj(tj):
                raise DocError(["timers must be an object"])
            timers = []
            for k, v in tj:
                ms = as_int(v)
                if ms is None:
                    raise DocError([f"timer {k}: bad delay"])
                if k == "":
                    errs.append("empty timer name")
                timers.append((k, ms))
            node = Node(name, parent, False, None, on, timers)
            if name not in self.nodes:
                self.nodes[name] = node

    # ------------------------------------------------------------ structure
    def is_message(self, n):
        x = self.nodes.get(n)
        return x is not None and not x.is_section

    def message_count(self):
        return sum(1 for n in self.nodes.values() if not n.is_section)

    def landing(self, n):
        """Entering node n: a section leads to its initial node, recursively."""
        seen = set()
        while self.nodes[n].is_section:
            if n in seen:
                raise RuntimeError("cycle of initials")
            seen.add(n)
            n = self.nodes[n].initial
        return n

    def enclosing(self, m):
        """Sections that contain message m, innermost first (root last)."""
        out = []
        p = self.nodes[m].parent
        while p is not None:
            out.append(p)
            p = self.nodes[p].parent
        return out

    def candidates(self, m, ev):
        """Decision 1: the message's own variants, then each section above."""
        out = []
        for owner in [m] + self.enclosing(m):
            for e, vs in self.nodes[owner].on:
                if e == ev:
                    out.extend(vs)
        return out

    # ------------------------------------------------------------ semantics
    def _enter(self, snap, acts, m, tok):
        for s in self.enclosing(m):          # decision 4
            if s in self.history:
                snap["resume"][s] = m
        for t, ms in self.nodes[m].timers:   # decision 2: timers after remember
            snap["timers"][t] = tok
            acts.append({"start_timer": t, "ms": ms, "token": tok})
        acts.append({"send": m})             # ... then send

    def _exit(self, snap, acts, m):
        names = [t for t, _ in self.nodes[m].timers]
        if self.opts.exit_cancels == "snapshot":
            # designer reading: also cancel every other timer still active in
            # the snapshot (left over from an older document version)
            names += [t for t in snap["timers"] if t not in names]
        if self.opts.bug == "no-exit":
            names = []
        for t in names:
            snap["timers"].pop(t, None)
            acts.append({"cancel_timer": t})

    def _resume_target(self, snap, sec, hits):
        r = snap["resume"].get(sec)
        fallback = self.landing(sec)
        if r is None or not self.is_message(r):
            return fallback                  # decision 5
        inside = sec in self.enclosing(r)
        if not inside:
            hits.add("A1-moved-memory")
            if self.opts.moved_memory == "follow":
                return r
            return fallback
        if self.opts.bug == "resume-initial":
            return fallback
        return r

    def _bump(self, snap):
        if snap["seq"] + 1 > MAX_INT:
            raise RequestError("seq_overflow")
        snap["seq"] += 1
        return snap["seq"]

    def start(self):
        snap = {"at": "", "resume": {}, "timers": {}, "seq": 0}
        acts = []
        tok = self._bump(snap)
        m = self.landing(self.ROOT)
        snap["at"] = m
        self._enter(snap, acts, m, tok)
        return snap, acts

    def _over(self, snap, keep_resume):
        snap = copy_snap(snap)
        acts = []
        tok = self._bump(snap)
        for t in list(snap["timers"].keys()):
            acts.append({"cancel_timer": t})
        snap["timers"] = {}
        if not keep_resume:
            snap["resume"] = {}
        m = self.landing(self.ROOT)
        snap["at"] = m
        self._enter(snap, acts, m, tok)
        return snap, acts

    def restart(self, snap):
        """format.md §8 (restart): cancel snapshot timers, clear places, first-entry actions,
        keep counting seq."""
        return self._over(snap, keep_resume=False)

    def accepts(self, snap, ev, token, hits):
        if token is None:
            return True
        t = ev[len("timer:"):]
        ok = snap["timers"].get(t) == token
        if self.opts.bug == "accept-any-token":
            ok = t in snap["timers"]
        owned = any(n == t for n, _ in self.nodes[snap["at"]].timers)
        if ok and not owned:
            hits.add("A7-unowned-timer-accepted")
            if self.opts.timer_accept == "owned":
                return False
        return ok

    def need_sets(self, snap, ev, token, given):
        """(minimal, all) sets of guard names the step needs and that are not
        in `given`.  Empty if the step is a recovery or an ignored timer."""
        if not self.is_message(snap["at"]):
            return set(), set()
        if not self.accepts(snap, ev, token, set()):
            return set(), set()
        cands = self.candidates(snap["at"], ev)
        if self.opts.bug == "need-own-only":
            cands = [v for e, vs in self.nodes[snap["at"]].on if e == ev for v in vs]
        allg = {v.cond for v in cands if v.cond is not None} - set(given)
        minimal = set()
        for v in cands:
            if v.cond is None:
                break
            if v.cond in given:
                if given[v.cond]:
                    break
                continue
            minimal.add(v.cond)
        return minimal, allg

    def step(self, snap, ev, token=None, guards=None, hits=None, check_need=True):
        """Returns ('ok', snap', actions, recovered) or ('need', set).
        With check_need=False the step is evaluated with absent guards = false
        (the driver only does this after checking the engine's `need`)."""
        hits = set() if hits is None else hits
        guards = guards or {}
        if token is not None and not (type(token) is int and 0 <= token <= MAX_INT):
            # strict protocol (docs/format.md, section 8): checked while decoding the request,
            # i.e. before recovery and before the token is compared
            raise RequestError(f"`token` must be an integer from 0 to {MAX_INT}")
        if not self.is_message(snap["at"]):          # decision 8
            s2, acts = self._over(snap, keep_resume=True)
            return ("ok", s2, acts, True)
        if not self.accepts(snap, ev, token, hits):  # decision 7
            return ("ok", copy_snap(snap), [], False)
        mn, al = self.need_sets(snap, ev, token, guards)
        want = al if self.opts.need == "all" else mn
        if self.opts.bug == "need-min":
            want = mn
        if mn != al:
            hits.add("A3-need-superset")
        if want and check_need:
            return ("need", want)
        chosen = None
        for v in self.candidates(snap["at"], ev):
            if v.cond is None or guards.get(v.cond, False):
                chosen = v
                break
            if self.opts.bug == "first-variant-only":
                break
        if chosen is None:                           # decision 6
            return ("ok", copy_snap(snap), [], False)
        if token is not None and not any(n == ev[6:] for n, _ in self.nodes[snap["at"]].timers):
            hits.add("A7-unowned-timer-transition")
        s2 = copy_snap(snap)
        acts = []
        tok = self._bump(s2)
        if chosen.resume:
            tgt = self._resume_target(snap, chosen.to, hits)
        else:
            tgt = self.landing(chosen.to)
        cur = snap["at"]
        self._exit(s2, acts, cur)                    # decision 2: exit ...
        calls = [{"call": c} for c in chosen.calls]
        if self.opts.bug == "calls-after-entry":
            pass
        else:
            acts.extend(calls)                       # ... arrow actions ...
        s2["at"] = tgt
        if self.opts.bug == "no-restart-self" and tgt == cur:
            acts.append({"send": tgt})
        else:
            self._enter(s2, acts, tgt, tok)          # ... entry
        if self.opts.bug == "calls-after-entry":
            acts.extend(calls)
        return ("ok", s2, acts, False)


def copy_snap(s):
    return {"at": s["at"], "resume": dict(s["resume"]), "timers": dict(s["timers"]), "seq": s["seq"]}


def snap_from_json(j):
    """Engine JSON snapshot -> oracle snapshot (dicts keep JSON order)."""
    if isinstance(j, list):  # pairs form
        d = dict(j)
    else:
        d = j
    res = d.get("resume", {})
    tim = d.get("timers", {})
    if isinstance(res, list):
        res = dict(res)
    if isinstance(tim, list):
        tim = dict(tim)
    return {"at": d["at"], "resume": dict(res), "timers": dict(tim), "seq": d.get("seq", 0)}


def snap_to_json(s):
    out = {"at": s["at"]}
    if s["resume"]:
        out["resume"] = dict(s["resume"])
    if s["timers"]:
        out["timers"] = dict(s["timers"])
    if s["seq"]:
        out["seq"] = s["seq"]
    return out

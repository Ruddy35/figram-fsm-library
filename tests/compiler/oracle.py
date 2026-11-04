"""Independent reference interpreter of Figram designer semantics.

Written from the PROSE of docs/format.md (sections 3-4, the decisions of the
specification 1-13 in section 7, section 8) and docs/host-contract.md -- NOT
from Figram/Chart.lean, Core.lean, Compile.lean or Engine.lean.  Wherever the
prose is ambiguous the interpreter has an explicit switch (see `Options`); the
defaults are the readings that the Lean implementation turned out to use, the
alternatives are what a literal reading of the prose (or the designer-level
model "a reminder lives on its message") would give.  The differential driver
counts how often each ambiguity is *hit* (the two readings would give
different observable results).

Document model (docs/format.md, section 4):
  section  <=> object has "children"; "initial" is a direct child (V2)
  message  has optional "timers": {name: ms}
  "on": {event: [variant]}, variant = {"if"?, "to"?, "resume"?, "back"?, "do"?}
  "calls": {name: text | {"text", "detached"?, "outcomes"?, "every"?, x-...}}
  any other key of the document, a node, a variant or an action starts with "x-" (section 5)
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
  * start with a first event (decision 9): matched as if the user stood on the
    initial message, which is neither entered nor left; no variant -> plain start;
  * timer event accepted only with the token of its current launch (7);
  * "back" (decision 10): a move whose two messages are not directly in the same
    section makes the message left the place to go back to (snapshot "back");
    a "back" variant leads there if it is a message, otherwise to "to" (or
    resumes "to" with "resume"); start/recovery leave no message and keep it;
    restart clears it;
  * snapshot pointing at a non-existent message -> start over: cancel all
    snapshot timers, keep remembered places, "recovered": true, and process a
    named event as at the initial message, like start with an event (8, 9);
  * background actions (decision 11): `{"call": a, "detached": true}`, nothing else changes;
  * outcomes (decision 12): a call of an action with outcomes carries `"token"` = the new
    `seq` and records the wait {call, token} at the front of `pending` (newest first);
    a new call of a non-`every` action replaces its waits; the event `call:<a>:<o>` with
    a token is accepted only if a wait (a, token) exists, accepting removes the wait and
    the outcome is then an ordinary event (arrows, bubbling; no arrow -> the snapshot
    without the wait, no actions); moves keep waits; restart clears them; recovery keeps
    them (an accepted outcome is matched at the initial message, a not accepted one
    gives the plain start over); `start` rejects outcome events;
  * arrows without a target (decision 13): the variant's actions, no exit/entry, seq + 1,
    places and timers unchanged except that a timer firing handled this way is removed
    from `timers`; in recovery / start with an event such a variant leads to the initial
    message (as a variant into the initial message does).
"""

import json

MAX_INT = 2 ** 53 - 1
FORMAT_DOC_KEYS = ["figram", "requires", "conditions", "calls", "variables", "bot"]
FORMAT_NODE_KEYS = ["initial", "children", "on", "timers"]
FORMAT_VARIANT_KEYS = ["if", "to", "resume", "back", "do"]
FORMAT_CALL_KEYS = ["text", "detached", "outcomes", "every"]
KNOWN_FEATURES = []
FAILED = "failed"


class DocError(Exception):
    def __init__(self, errors):
        super().__init__("; ".join(errors))
        self.errors = errors


class RequestError(Exception):
    """A request error. `code` is the protocol code (docs/format.md, section 8, "Errors");
    `text` is compared too when the oracle knows it (only for the legacy texts)."""

    def __init__(self, text, code=None):
        super().__init__(text)
        self.text = text
        self.code = code or ("seq_overflow" if text == "seq_overflow" else "bad_request")


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
        # A8: a timer of the message being left that is not in the snapshot (consumed by an
        #     arrow without a target, decision 13, or absent in an older snapshot): 'cancel'
        #     (decision 2: "cancel the timers of the message being left", by the document) |
        #     'skip' (only timers the snapshot holds are cancelled)
        self.cancel_absent = kw.pop("cancel_absent", "cancel")
        # A9: order of the waits created by ONE step whose `do` holds two actions with
        #     outcomes (both get the same token; "the newest first"): 'reverse' (each call is
        #     put in front in turn: the later call first) | 'do' (the step's waits in `do`
        #     order, in front of the older ones)
        self.wait_order = kw.pop("wait_order", "reverse")
        # A10: a new call of a non-`every` action when the snapshot holds SEVERAL waits for it
        #     (a snapshot of a version where it had `every`): 'all' (every old wait of the
        #     action is replaced) | 'one' (only the newest)
        self.replace = kw.pop("replace", "all")
        # A11: accepting an outcome when the snapshot holds the same wait (action, token)
        #     twice (only a forged snapshot): 'all' (every copy is removed) | 'one' ("each
        #     wait accepts one outcome": the newest copy)
        self.dup_accept = kw.pop("dup_accept", "all")
        # A12: an outcome name the action does not declare (`call:a:bogus` with the token of
        #     a wait of `a`): 'any' (rule 2: accepted by the wait alone) | 'declared'
        self.outcome_names = kw.pop("outcome_names", "any")
        # A13: `"outcomes": []` in a declaration: 'none' (an explicitly written default: no
        #     outcomes) | 'failed-only' (an action with outcomes whose only outcome is
        #     `failed`) | 'error'
        self.empty_outcomes = kw.pop("empty_outcomes", "none")
        # A14: recovery with an ACCEPTED outcome that matches no variant: 'remove' (rule 2:
        #     the wait is removed, then the plain start over) | 'keep'
        self.recovery_unmatched = kw.pop("recovery_unmatched", "remove")
        # A15: a new wait (action, token) when the snapshot already holds the same wait (only a
        #     forged snapshot: its token is not below the new seq) for an `every` action:
        #     'replace' (the old copy is dropped, the new one is put in front) | 'keep'
        self.same_wait = kw.pop("same_wait", "replace")
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


def split_outcome(ev):
    """`call:<action>:<outcome>` split at the LAST `:` (docs/format.md, section 4), both
    parts non-empty -> (action, outcome); otherwise None."""
    if not ev.startswith("call:"):
        return None
    rest = ev[len("call:"):]
    i = rest.rfind(":")
    if i <= 0 or i == len(rest) - 1:
        return None
    return rest[:i], rest[i + 1:]


def outcome_event(action, outcome):
    return f"call:{action}:{outcome}"


class Node:
    __slots__ = ("name", "parent", "is_section", "initial", "on", "timers")

    def __init__(self, name, parent, is_section, initial, on, timers):
        self.name, self.parent, self.is_section = name, parent, is_section
        self.initial, self.on, self.timers = initial, on, timers


class Variant:
    __slots__ = ("cond", "to", "resume", "calls", "back")

    def __init__(self, cond, to, resume, calls, back=False):
        # to = None: an arrow without a target (decision 13)
        self.cond, self.to, self.resume, self.calls, self.back = cond, to, resume, calls, back


class Call:
    __slots__ = ("name", "detached", "outcomes", "every", "empty")

    def __init__(self, name, detached=False, outcomes=None, every=False, empty=False):
        # outcomes = None: an action without outcomes; otherwise the declared list;
        # empty: declared with `"outcomes": []` (ambiguity A13)
        self.name, self.detached, self.outcomes, self.every = name, detached, outcomes, every
        self.empty = empty


class Bot:
    """A checked document plus the step semantics."""

    ROOT = ""  # the bot root section has no name of its own

    def __init__(self, doc, opts=None):
        self.opts = opts or Options()
        if isinstance(doc, (str, bytes)):
            doc = parse_json(doc)
        self.nodes = {}
        self.conditions, self.calls, self.decls = [], [], {}
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

    def _calls(self, doc, errs):
        """`calls`: an action is a text or {"text", "detached"?, "outcomes"?, "every"?}
        (section 4, decisions 9, 11, 13 of section 3)."""
        m = get(doc, "calls", [])
        if not is_obj(m):
            raise DocError(["calls: not an object"])
        for k, v in m:
            if k == "":
                errs.append("calls: empty name")
            if isinstance(v, str):
                self.decls[k] = Call(k)
                self.calls.append(k)
                continue
            if not is_obj(v):
                raise DocError([f"action {k!r}: a text or an object expected"])
            self._extra(v, FORMAT_CALL_KEYS, f"action {k!r}", errs)
            if not isinstance(get(v, "text"), str):
                raise DocError([f"action {k!r}: `text` expected"])
            det = get(v, "detached", False)
            if not isinstance(det, bool):
                raise DocError([f"action {k!r}: detached: bool expected"])
            every = get(v, "every", False)
            if not isinstance(every, bool):
                raise DocError([f"action {k!r}: every: bool expected"])
            outs = None
            empty = has(v, "outcomes") and get(v, "outcomes") == []
            if has(v, "outcomes"):
                outs = get(v, "outcomes")
                if not isinstance(outs, list) or not all(isinstance(o, str) for o in outs):
                    raise DocError([f"action {k!r}: outcomes: a list of names expected"])
                for o in outs:
                    if o == "":
                        errs.append(f"action {k!r}: empty outcome name")
                    elif ":" in o:
                        errs.append(f"action {k!r}: outcome {o!r} contains ':'")
                    elif o == FAILED:
                        errs.append(f"action {k!r}: `failed` must not be listed")
                if len(set(outs)) != len(outs):
                    errs.append(f"action {k!r}: duplicate outcome")
                if not outs:
                    if self.opts.empty_outcomes == "error":
                        errs.append(f"action {k!r}: empty outcomes")
                    elif self.opts.empty_outcomes == "none":
                        outs = None
                outs = list(outs) if outs is not None else None
            if every and outs is None:
                errs.append(f"action {k!r}: `every` needs `outcomes`")
            self.decls[k] = Call(k, det, outs, every, empty)
            self.calls.append(k)

    @staticmethod
    def _extra(obj, known, where, errs):
        for k, _ in obj:
            if k not in known and not k.startswith("x-"):
                errs.append(f"{where}: unknown key {k!r}")

    def _load(self, doc, errs):
        if not is_obj(doc):
            raise DocError(["document must be an object"])
        self._extra(doc, FORMAT_DOC_KEYS, "document", errs)
        fig = get(doc, "figram")
        if as_int(fig) != 1:
            raise DocError(["figram must be 1"])
        req = get(doc, "requires", [])
        if not isinstance(req, list) or not all(isinstance(r, str) for r in req):
            raise DocError(["requires must be a list of strings"])
        if any(r not in KNOWN_FEATURES for r in req):
            raise DocError(["unknown required feature"])
        self.conditions = self._strmap(doc, "conditions", errs)
        self._calls(doc, errs)
        self._strmap(doc, "variables", errs)
        bot = get(doc, "bot")
        if bot is None:
            raise DocError(["no bot"])
        if not is_obj(bot) or not has(bot, "children"):
            raise DocError(["bot must be a section"])
        self._node(self.ROOT, None, bot, errs, top=True)
        # V2: initial is a direct child; V3-V6 references
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
                if ev.startswith("call:"):                       # V5, the reserved prefix
                    sp = split_outcome(ev)
                    d = self.decls.get(sp[0]) if sp else None
                    if sp is None or d is None or d.outcomes is None or \
                            (sp[1] != FAILED and sp[1] not in d.outcomes):
                        errs.append(f"event {ev!r}: not an outcome of an action with outcomes")
                for v in vs:
                    if v.to is None:
                        if v.resume or v.back:                   # V3
                            errs.append("`resume` and `back` need `to`")
                    else:
                        tgt = self.nodes.get(v.to)
                        if v.to == "" or tgt is None:
                            errs.append(f"target {v.to!r} does not exist")
                        elif v.resume and not tgt.is_section:
                            errs.append(f"resume target {v.to!r} is not a section")
                    if v.cond is not None and (v.cond == "" or v.cond not in self.conditions):
                        errs.append(f"condition {v.cond!r} undeclared")
                    seen = set()
                    for c in v.calls:
                        if c == "" or c not in self.decls:
                            errs.append(f"call {c!r} undeclared")
                        elif self.decls[c].outcomes is not None:   # V6
                            if c in seen:
                                errs.append(f"action {c!r} with outcomes twice in one `do`")
                            seen.add(c)

    def _node(self, name, parent, obj, errs, top=False):
        if not is_obj(obj):
            raise DocError([f"node {name!r} must be an object"])
        self._extra(obj, FORMAT_NODE_KEYS, f"node {name!r}", errs)
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
                self._extra(vj, FORMAT_VARIANT_KEYS, f"{name}.{ev}", errs)
                cond = get(vj, "if")
                if cond is not None and not isinstance(cond, str):
                    raise DocError(["if: string expected"])
                if has(vj, "if") and cond is None:
                    raise DocError(["if: null"])
                to = get(vj, "to")
                if has(vj, "to") and not isinstance(to, str):
                    raise DocError(["to: string expected"])
                res = get(vj, "resume", False)
                if not isinstance(res, bool):
                    raise DocError(["resume: bool expected"])
                bk = get(vj, "back", False)
                if not isinstance(bk, bool):
                    raise DocError(["back: bool expected"])
                do = get(vj, "do", [])
                if not isinstance(do, list) or not all(isinstance(c, str) for c in do):
                    raise DocError(["do: list of strings expected"])
                vs.append(Variant(cond, to, res, list(do), bk))
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

    def outcome_actions(self):
        """Actions with outcomes of this document: {name: [outcomes + failed]}."""
        return {k: list(d.outcomes) + [FAILED] for k, d in self.decls.items() if d.outcomes is not None}

    # ------------------------------------------------------------ events
    def classify(self, ev, token, for_start=False):
        """Request decoding (docs/format.md, section 8): ('named'|'timer'|'outcome', info).
        `token` is allowed only with (and required for) a timer or an outcome event."""
        if not isinstance(ev, str) or ev == "":
            raise RequestError("`event` must be a non-empty string")
        if ev.startswith("timer:"):
            if for_start:
                raise RequestError("a timer event cannot start a user")
            if ev == "timer:":
                raise RequestError("empty timer name in `event`")
            kind, info = "timer", ev[len("timer:"):]
        elif ev.startswith("call:"):
            if for_start:
                raise RequestError("an outcome event cannot start a user")
            sp = split_outcome(ev)
            if sp is None:
                raise RequestError("an outcome event is `call:<action>:<outcome>`, both parts non-empty")
            kind, info = "outcome", sp
        else:
            kind, info = "named", None
        if for_start:
            return kind, info
        if kind == "named":
            if token is not None:
                raise RequestError("`token` is allowed only with a timer or an outcome event")
        else:
            if token is None:
                raise RequestError("missing field `token`")
            if not (type(token) is int and 0 <= token <= MAX_INT):
                # strict protocol (docs/format.md, section 8): checked while decoding the
                # request, i.e. before recovery and before the token is compared
                raise RequestError(f"`token` must be an integer from 0 to {MAX_INT}")
        return kind, info

    # ------------------------------------------------------------ semantics
    def _enter(self, snap, acts, m, tok):
        for s in self.enclosing(m):          # decision 4
            if s in self.history:
                snap["resume"][s] = m
        if self.opts.bug == "entry-skips-timers":
            acts.append({"send": m})
            return
        for t, ms in self.nodes[m].timers:   # decision 2: timers after remember
            snap["timers"][t] = tok
            acts.append({"start_timer": t, "ms": ms, "token": tok})
        acts.append({"send": m})             # ... then send

    def _exit(self, snap, acts, m, hits):
        names = [t for t, _ in self.nodes[m].timers]
        absent = [t for t in names if t not in snap["timers"]]
        if absent:
            hits.add("A8-exit-cancels-absent-timer")
            if self.opts.cancel_absent == "skip":
                names = [t for t in names if t in snap["timers"]]
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

    def _target(self, snap, v, hits):
        """Where variant v leads: "back" to the remembered message if it is still a
        message (decision 10), otherwise "to" as usual (decisions 4, 5)."""
        if v.back and self.opts.bug != "back-ignored":
            b = snap.get("back")
            if b is not None and self.is_message(b):
                hits.add("back-taken")
                return b
            hits.add("back-fallback")
        if v.resume:
            return self._resume_target(snap, v.to, hits)
        return self.landing(v.to)

    def _mark_back(self, s2, cur, tgt):
        """Decision 10: crossing a section boundary remembers the message left."""
        if self.opts.bug == "back-not-recorded":
            return
        if self.nodes[cur].parent != self.nodes[tgt].parent or self.opts.bug == "back-every-move":
            s2["back"] = cur

    def _bump(self, snap):
        if snap["seq"] + 1 > MAX_INT:
            raise RequestError("seq_overflow")
        snap["seq"] += 1
        return snap["seq"]

    def _call_actions(self, s2, v, tok, hits):
        """The variant's actions (decisions 11, 12): `{"call": a, "token"?: tok,
        "detached"?: true}`; every call of an action with outcomes records a wait."""
        acts, waits = [], []
        for c in v.calls:
            d = self.decls[c]
            if d.empty:
                hits.add("A13-empty-outcomes-called")
            a = {"call": c}
            if d.outcomes is not None and self.opts.bug != "no-call-token":
                a["token"] = tok
            if d.detached and self.opts.bug != "no-detached-flag":
                a["detached"] = True
            acts.append(a)
            if d.outcomes is not None:
                waits.append(c)
        if len(waits) >= 2:
            hits.add("A9-two-waits-in-one-step")
        pend = s2["pending"]
        for c in waits:
            if self.decls[c].every and self.opts.bug != "every-replaces":
                same = [w for w in pend if w["call"] == c and w["token"] == tok]
                if same:
                    hits.add("A15-same-wait-exists")
                    if self.opts.same_wait == "replace":
                        pend[:] = [w for w in pend if not (w["call"] == c and w["token"] == tok)]
                continue
            if self.opts.bug == "latest-keeps-old":
                continue
            old = [w for w in pend if w["call"] == c]
            if len(old) >= 2:
                hits.add("A10-replace-several-waits")
                if self.opts.replace == "one":
                    pend.remove(old[0])
                    continue
            if old:
                hits.add("wait-replaced")
            pend[:] = [w for w in pend if w["call"] != c]
        new = [{"call": c, "token": tok} for c in waits]
        if self.opts.wait_order == "reverse":
            new.reverse()
        if self.opts.bug == "pending-oldest-first":
            pend.extend(new)
        else:
            pend[:0] = new
        return acts

    def start(self):
        snap = {"at": "", "resume": {}, "timers": {}, "seq": 0, "back": None, "pending": []}
        acts = []
        tok = self._bump(snap)
        m = self.landing(self.ROOT)
        snap["at"] = m
        self._enter(snap, acts, m, tok)
        return snap, acts

    def _first(self, cands, guards):
        for v in cands:
            if v.cond is None or guards.get(v.cond, False):
                return v
            if self.opts.bug == "first-variant-only":
                return None
        return None

    def start_with(self, ev, guards=None, seq=0, check_need=True, hits=None):
        """format.md §7, decision 9, and §8 (start with `event`): the first entry with the
        user's first event, matched as if the user stood on the initial message; that message
        is neither entered nor left (not sent, its timers not started, not remembered).
        Returns ('ok', snap, actions) or ('need', set)."""
        hits = set() if hits is None else hits
        guards = guards or {}
        self.classify(ev, None, for_start=True)   # a timer or an outcome event cannot start a user
        init = self.landing(self.ROOT)
        here = {"at": init, "resume": {}, "timers": {}, "seq": seq, "back": None, "pending": []}
        mn, al = self.need_sets(here, ev, None, guards)
        want = al if self.opts.need == "all" else mn
        if want and check_need:
            return ("need", want)
        chosen = self._first(self.candidates(init, ev), guards)
        snap = {"at": "", "resume": {}, "timers": {}, "seq": seq, "back": None, "pending": []}
        acts = []
        tok = self._bump(snap)
        if self.opts.bug == "start-enters-initial" and chosen is not None:
            self._enter(snap, acts, init, tok)   # deliberately wrong: the initial message is entered
        if chosen is None:                       # no variant: the plain first entry
            tgt = init
        else:
            if chosen.to is None:
                hits.add("stay-at-start")
                if self.opts.bug == "stay-start-plain":
                    chosen = Variant(None, None, False, [])
            # decision 13: without a target the variant leads to the initial message
            tgt = init if chosen.to is None else self._target(snap, chosen, hits)
            acts.extend(self._call_actions(snap, chosen, tok, hits))
        snap["at"] = tgt
        self._enter(snap, acts, tgt, tok)
        return ("ok", snap, acts)

    def _over(self, snap, keep_resume):
        snap = copy_snap(snap)
        acts = []
        tok = self._bump(snap)
        for t in list(snap["timers"].keys()):
            acts.append({"cancel_timer": t})
        snap["timers"] = {}
        if not keep_resume:
            snap["resume"] = {}
            snap["back"] = None
            if self.opts.bug != "restart-keeps-waits":
                snap["pending"] = []
        elif self.opts.bug == "recovery-clears-waits":
            snap["pending"] = []
        m = self.landing(self.ROOT)
        snap["at"] = m
        self._enter(snap, acts, m, tok)
        return snap, acts

    def restart(self, snap):
        """format.md §8 (restart): cancel snapshot timers, clear places, the place to go
        back to and the waits, first-entry actions, keep counting seq."""
        return self._over(snap, keep_resume=False)

    def accepts(self, snap, ev, token, hits):
        """Decision 7 for a timer event (snap["at"] is a message)."""
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

    def accept_outcome(self, snap, action, outcome, token, hits):
        """Decision 12, rule 2 of a step: the snapshot without the wait (action, token), or
        None if `pending` has no such wait (the event is ignored)."""
        pend = snap["pending"]
        match = [i for i, w in enumerate(pend) if w["call"] == action and w["token"] == token]
        if self.opts.bug == "outcome-any-token":
            match = [i for i, w in enumerate(pend) if w["call"] == action][:1]
        if not match:
            if any(w["call"] == action for w in pend):
                hits.add("outcome-stale-token")
            return None
        d = self.decls.get(action)
        declared = d is not None and d.outcomes is not None and (outcome == FAILED or outcome in d.outcomes)
        if not declared:
            hits.add("A12-undeclared-outcome-accepted")
            if self.opts.outcome_names == "declared":
                return None
        if d is None or d.outcomes is None:
            hits.add("wait-of-action-without-outcomes")
        s2 = copy_snap(snap)
        if len(match) >= 2:
            hits.add("A11-duplicate-wait")
            if self.opts.dup_accept == "one":
                match = match[:1]
        if self.opts.bug != "outcome-keeps-wait":
            s2["pending"] = [w for i, w in enumerate(pend) if i not in match]
        return s2

    def _accepted(self, snap, kind, info, ev, token, hits):
        """The snapshot after accepting the event, or None if it is ignored."""
        if kind == "timer":
            return copy_snap(snap) if self.accepts(snap, ev, token, hits) else None
        if kind == "outcome":
            return self.accept_outcome(snap, info[0], info[1], token, hits)
        return copy_snap(snap)

    def need_sets(self, snap, ev, token, given):
        """(minimal, all) sets of guard names the step needs and that are not
        in `given`.  Empty for an ignored timer or outcome; for a recovery, those of the
        initial message's arrow (decisions 8, 9 and 12; none for a timer event or an
        outcome that is not accepted)."""
        try:
            kind, info = self.classify(ev, token)
        except RequestError:
            return set(), set()
        if not self.is_message(snap["at"]):
            if kind == "timer" or self.opts.bug == "recover-drops-event":
                return set(), set()
            if kind == "outcome":
                if self.opts.bug == "recovery-ignores-outcome":
                    return set(), set()
                if self.accept_outcome(snap, info[0], info[1], token, set()) is None:
                    return set(), set()
            snap = {"at": self.landing(self.ROOT), "resume": {}, "timers": {}, "seq": snap["seq"],
                    "back": snap.get("back"), "pending": []}
        elif self._accepted(snap, kind, info, ev, token, set()) is None:
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

    def _recover(self, snap, ev, kind, info, token, guards, hits, check_need):
        """Decisions 8, 9, 12, 13: the user starts over; a named event or an accepted
        outcome is matched as at the initial message."""
        init = self.landing(self.ROOT)
        chosen = None
        base = snap
        matchable = kind == "named" and self.opts.bug != "recover-drops-event"
        if kind == "outcome" and self.opts.bug != "recover-drops-event":
            acc = self.accept_outcome(snap, info[0], info[1], token, hits)
            if acc is not None:
                hits.add("recovery-accepted-outcome")
                base = acc
                matchable = self.opts.bug != "recovery-ignores-outcome"
            else:
                hits.add("recovery-outcome-not-accepted")
        if matchable:
            mn, al = self.need_sets(snap, ev, token, guards)
            want = al if self.opts.need == "all" else mn
            if want and check_need:
                return ("need", want)
            chosen = self._first(self.candidates(init, ev), guards)
        if chosen is None:
            if kind == "outcome" and base is not snap:
                hits.add("A14-recovery-accepted-unmatched")
                if self.opts.recovery_unmatched == "keep":
                    base = snap
            s2, acts = self._over(base, keep_resume=True)
            return ("ok", s2, acts, True)
        s2 = copy_snap(base)
        tok = self._bump(s2)
        acts = [{"cancel_timer": t} for t in s2["timers"]]
        s2["timers"] = {}
        if chosen.to is None:
            hits.add("stay-in-recovery")
            tgt = init                       # decision 13: leads to the initial message
        else:
            tgt = self._target(base, chosen, hits)
        acts.extend(self._call_actions(s2, chosen, tok, hits))
        s2["at"] = tgt
        self._enter(s2, acts, tgt, tok)
        return ("ok", s2, acts, True)

    def step(self, snap, ev, token=None, guards=None, hits=None, check_need=True):
        """Returns ('ok', snap', actions, recovered) or ('need', set).
        With check_need=False the step is evaluated with absent guards = false
        (the driver only does this after checking the engine's `need`)."""
        hits = set() if hits is None else hits
        guards = guards or {}
        kind, info = self.classify(ev, token)
        if not self.is_message(snap["at"]):          # decisions 8 and 9
            return self._recover(snap, ev, kind, info, token, guards, hits, check_need)
        acc = self._accepted(snap, kind, info, ev, token, hits)
        if acc is None:                              # decisions 7 and 12
            return ("ok", copy_snap(snap), [], False)
        mn, al = self.need_sets(snap, ev, token, guards)
        want = al if self.opts.need == "all" else mn
        if self.opts.bug == "need-min":
            want = mn
        if mn != al:
            hits.add("A3-need-superset")
        if want and check_need:
            return ("need", want)
        chosen = self._first(self.candidates(snap["at"], ev), guards)
        if chosen is None:                           # decision 6 (an outcome: its wait is gone)
            if kind == "outcome":
                hits.add("outcome-without-arrow")
                if self.opts.bug == "outcome-no-arrow-keeps-wait":
                    return ("ok", copy_snap(snap), [], False)
            return ("ok", acc, [], False)
        if kind == "timer" and not any(n == ev[6:] for n, _ in self.nodes[snap["at"]].timers):
            hits.add("A7-unowned-timer-transition")
        if kind == "outcome":
            hits.add("outcome-handled")
        s2 = copy_snap(acc)
        acts = []
        cur = snap["at"]
        if chosen.to is None and self.opts.bug != "stay-exits":   # decision 13
            if self.opts.bug != "stay-no-seq":
                tok = self._bump(s2)
            else:
                tok = s2["seq"]
            hits.add("stay")
            acts.extend(self._call_actions(s2, chosen, tok, hits))
            if kind == "timer" and self.opts.bug != "stay-keeps-timer":
                hits.add("stay-consumes-timer")
                s2["timers"].pop(info, None)
            return ("ok", s2, acts, False)
        tok = self._bump(s2)
        tgt = cur if chosen.to is None else self._target(snap, chosen, hits)
        self._mark_back(s2, cur, tgt)
        self._exit(s2, acts, cur, hits)              # decision 2: exit ...
        if self.opts.bug == "move-clears-waits":
            s2["pending"] = []
        calls = self._call_actions(s2, chosen, tok, hits)
        if self.opts.bug != "calls-after-entry":
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
    return {"at": s["at"], "resume": dict(s["resume"]), "timers": dict(s["timers"]), "seq": s["seq"],
            "back": s.get("back"), "pending": [dict(w) for w in s.get("pending", [])]}


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
    pend = []
    for w in d.get("pending", []):
        w = dict(w) if isinstance(w, list) else w
        pend.append({"call": w["call"], "token": w["token"]})
    return {"at": d["at"], "resume": dict(res), "timers": dict(tim), "seq": d.get("seq", 0),
            "back": d.get("back"), "pending": pend}


def snap_to_json(s):
    """Snapshot in the field order of docs/format.md, section 8 (defaults omitted)."""
    out = {"at": s["at"]}
    if s.get("back") is not None:
        out["back"] = s["back"]
    if s["resume"]:
        out["resume"] = dict(s["resume"])
    if s["timers"]:
        out["timers"] = dict(s["timers"])
    if s.get("pending"):
        out["pending"] = [{"call": w["call"], "token": w["token"]} for w in s["pending"]]
    if s["seq"]:
        out["seq"] = s["seq"]
    return out

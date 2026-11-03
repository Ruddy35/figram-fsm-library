"""Random walks through figram-engine: every reply is validated by
schemas/snapshot.schema.json (jsonschema), clients/python/protocol.check_reply,
and checked against the document (call carries token iff the action has outcomes,
detached iff declared detached; pending grows by the calls with a token; tokens == seq).
Also writes every (op, reply line) into a file for the Go validator.

usage: walk.py REPO DOC... [--steps N] [--dump FILE]
"""
import json, os, random, subprocess, sys

repo = sys.argv[1]
args = sys.argv[2:]
steps = 400
dump = None
docs = []
i = 0
while i < len(args):
    if args[i] == "--steps":
        steps = int(args[i + 1]); i += 2
    elif args[i] == "--dump":
        dump = open(args[i + 1], "w"); i += 2
    else:
        docs.append(args[i]); i += 1

sys.path.insert(0, os.path.join(repo, "clients", "python"))
import protocol  # noqa
import jsonschema  # noqa

snap_schema = json.load(open(os.path.join(repo, "schemas", "snapshot.schema.json")))
doc_schema = json.load(open(os.path.join(repo, "schemas", "figram.schema.json")))
SV = jsonschema.Draft202012Validator(snap_schema)
DV = jsonschema.Draft202012Validator(doc_schema)
ENGINE = os.path.join(repo, ".lake", "build", "bin", "figram-engine")

problems = []


def note(msg):
    problems.append(msg)
    if len(problems) < 30:
        print("PROBLEM:", msg)


def events_of(node, acc):
    for e in (node.get("on") or {}):
        acc.add(e)
    for c in (node.get("children") or {}).values():
        events_of(c, acc)
    return acc


def conds_of(doc):
    return list((doc.get("conditions") or {}).keys())


class Eng:
    def __init__(self, doc):
        self.p = subprocess.Popen([ENGINE, doc], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        self.n = 0
        self.doc = doc

    def req(self, r):
        self.n += 1
        r = dict(r, id=self.n)
        reqline = json.dumps(r)
        self.p.stdin.write((reqline + "\n").encode())
        self.p.stdin.flush()
        line = self.p.stdout.readline()
        if dump:
            dump.write(json.dumps({"op": r.get("op"), "id": self.n, "line": line.decode(), "doc": self.doc, "req": reqline}) + "\n")
        resp = json.loads(line)
        try:
            v = protocol.check_reply(resp, self.n, r.get("op"))
        except protocol.ProtocolError as e:
            note(f"check_reply rejected engine reply {line!r} to {r}: {e}")
            v = None
        return resp, v

    def close(self):
        self.p.stdin.close()
        self.p.wait(10)


total = 0
for docpath in docs:
    doc = json.load(open(docpath))
    errs = list(DV.iter_errors(doc))
    if errs:
        note(f"{docpath}: schema rejects a document the engine loads: {errs[0].message}")
    calls = doc.get("calls") or {}

    def decl(c):
        d = calls.get(c)
        if isinstance(d, dict):
            return bool(d.get("detached")), bool(d.get("outcomes")), list(d.get("outcomes") or []), bool(d.get("every"))
        return False, False, [], False
    evs = sorted(events_of(doc["bot"], set()))
    conds = conds_of(doc)
    eng = Eng(docpath)
    rnd = random.Random(hash(docpath) & 0xffff)
    hello, v = eng.req({"op": "hello"})
    if v != "hello":
        note(f"hello: {hello}")

    def check_result(prev, resp, ev, tok):
        s = resp["snapshot"]
        for e in SV.iter_errors(s):
            note(f"snapshot schema rejects engine snapshot {s}: {e.message}")
            break
        seq = s.get("seq", 0)
        pend = [(w["call"], w["token"]) for w in s.get("pending", [])]
        for a in resp["actions"]:
            if "call" in a:
                d, has_out, outs, every = decl(a["call"])
                if ("token" in a) != has_out:
                    note(f"call {a} token presence vs declaration outcomes={has_out}")
                if (a.get("detached") is True) != d:
                    note(f"call {a} detached vs declaration {d}")
                if "token" in a:
                    if a["token"] != seq:
                        note(f"call token {a['token']} != seq {seq}")
                    if (a["call"], a["token"]) not in pend:
                        note(f"call {a} has no wait in pending {pend}")
            if "start_timer" in a and a["token"] != seq:
                note(f"timer token {a} != seq {seq}")
        if len(set(pend)) != len(pend):
            note(f"duplicate waits {pend}")
        return s

    for walk in range(max(1, steps // 40)):
        # start, sometimes with an event
        if rnd.random() < 0.5:
            r, v = eng.req({"op": "start", "seq": rnd.choice([0, 5, 1000])})
        else:
            e = rnd.choice([x for x in evs if not x.startswith(("timer:", "call:"))] or ["cmd:start"])
            r, v = eng.req({"op": "start", "event": e, "guards": {c: rnd.random() < 0.5 for c in conds}})
        total += 1
        if v != "result":
            note(f"start: {r}")
            continue
        snap = check_result(None, r, None, None)
        for k in range(40):
            total += 1
            choice = rnd.random()
            g = {c: rnd.random() < 0.5 for c in conds}
            if choice < 0.25 and snap.get("pending"):
                w = rnd.choice(snap["pending"])
                d, has_out, outs, every = decl(w["call"])
                o = rnd.choice(outs + ["failed"]) if outs else "failed"
                tok = w["token"] if rnd.random() < 0.85 else w["token"] + rnd.choice([-1, 1])
                tok = max(tok, 0)
                req = {"op": "step", "snapshot": snap, "event": f"call:{w['call']}:{o}", "token": tok, "guards": g}
            elif choice < 0.40 and snap.get("timers"):
                t, tok = rnd.choice(sorted(snap["timers"].items()))
                req = {"op": "step", "snapshot": snap, "event": f"timer:{t}", "token": tok, "guards": g}
            elif choice < 0.43:
                req = {"op": "restart", "snapshot": snap}
            else:
                e = rnd.choice([x for x in evs if not x.startswith(("timer:", "call:"))] or ["cmd:start"])
                req = {"op": "step", "snapshot": snap, "event": e, "guards": g}
            r, v = eng.req(req)
            if v == "result":
                prevpend = [(w["call"], w["token"]) for w in snap.get("pending", [])]
                snap2 = check_result(snap, r, req.get("event"), req.get("token"))
                if req["op"] == "restart" and snap2.get("pending"):
                    note(f"restart kept waits {snap2}")
                ev = req.get("event", "")
                if ev.startswith("call:") and req["op"] == "step":
                    c = ev[5:].rpartition(":")[0]
                    newpend = [(w["call"], w["token"]) for w in snap2.get("pending", [])]
                    if (c, req["token"]) in prevpend and (c, req["token"]) in newpend and not any(
                            a.get("call") == c and a.get("token") == req["token"] for a in r["actions"]):
                        note(f"accepted outcome kept its wait: {req} -> {r}")
                    if (c, req["token"]) not in prevpend and r["snapshot"] != snap and not r.get("recovered"):
                        note(f"not-accepted outcome changed the snapshot: {req} -> {r}")
                snap = snap2
            elif v == "need":
                note(f"need with all guards given: {req} -> {r}")
            else:
                note(f"error reply: {req} -> {r}")
    eng.close()

print(f"docs {len(docs)}, requests {total}, problems {len(problems)}")
sys.exit(1 if problems else 0)

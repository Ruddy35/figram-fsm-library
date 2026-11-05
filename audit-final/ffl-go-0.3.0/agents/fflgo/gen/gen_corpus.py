"""Generate engine replies (valid corpus) and mutated replies (mutant corpus).

    python3 -I gen_corpus.py OUTDIR [scale]

For every document: `figram trace doc N LEN SEED --directed` gives scenarios; each scenario is
replayed as protocol requests against `figram-engine doc` (the snapshot of the previous engine
reply is passed on unchanged, as text). Extra requests per step: the step without guards (need),
outcome events with stale/zero/repeated tokens, a recovered variant, restart, hello and error
requests. The replies, paired with their requests by id, are the valid corpus; mutations of them
are the mutant corpus. Documents: the examples, docs/order.bot.json (written for this audit),
`figram random` documents and transformed random documents (':' in action and timer names,
non-ASCII and HTML characters in names).
"""
import json, os, random, subprocess, sys

FIG = "/home/user/figram-audit-worktrees/cleanup-int"
BIN = FIG + "/.lake/build/bin"
OUT = sys.argv[1]
SCALE = int(sys.argv[2]) if len(sys.argv) > 2 else 1
MAXINT = 2**53 - 1
os.makedirs(OUT + "/docs", exist_ok=True)


def run(cmd, data=None, timeout=300):
    r = subprocess.run(cmd, input=data, capture_output=True, timeout=timeout)
    return r


def dumps(v):
    return json.dumps(v, separators=(",", ":"), ensure_ascii=False)


def transform(doc, salt):
    """Rename actions to names with ':' (and some non-ASCII/HTML characters) and timers to names
    with ':'; rewrite all references (do lists, call:/timer: events)."""
    calls = doc.get("calls", {})
    amap = {}
    for i, k in enumerate(list(calls)):
        amap[k] = [f"ns:{k}", f"{k}:x:y", f"é{k}&<{k}>", f"{k}:"][(i + salt) % 4]
    doc["calls"] = {amap[k]: v for k, v in calls.items()}
    tmap = {}

    def node(n):
        if "timers" in n:
            nt = {}
            for t, ms in n["timers"].items():
                tmap.setdefault(t, f"t:{t}")
                nt[tmap[t]] = ms
            n["timers"] = nt
        for c in n.get("children", {}).values():
            node(c)

    node(doc["bot"])

    def ev(e):
        if e.startswith("call:"):
            body = e[5:]
            a, o = body.rsplit(":", 1)
            return f"call:{amap.get(a, a)}:{o}"
        if e.startswith("timer:"):
            t = e[6:]
            return "timer:" + tmap.get(t, t)
        return e

    def arrows(n):
        if "on" in n:
            n["on"] = {ev(e): [dict(v, **({"do": [amap.get(x, x) for x in v["do"]]} if "do" in v else {}))
                               for v in vs] for e, vs in n["on"].items()}
        for c in n.get("children", {}).values():
            arrows(c)

    arrows(doc["bot"])
    return doc


def documents():
    docs = []
    for name in ("pizza", "features", "annotations"):
        docs.append((name, f"{FIG}/examples/{name}.bot.json", 80 * SCALE, 30))
    docs.append(("order", "/home/user/figram-audit/agents7/fflgo/docs/order.bot.json", 50 * SCALE, 25))
    for seed in range(1, 1 + 20 * SCALE):
        msgs = 6 + seed % 9
        r = run([f"{BIN}/figram", "random", str(seed), str(msgs)])
        if r.returncode != 0:
            continue
        p = f"{OUT}/docs/random-{seed}.bot.json"
        open(p, "wb").write(r.stdout)
        docs.append((f"random-{seed}", p, 20, 25))
        t = transform(json.loads(r.stdout), seed)
        p2 = f"{OUT}/docs/colon-{seed}.bot.json"
        open(p2, "w").write(json.dumps(t, ensure_ascii=False, indent=1))
        c = run([f"{BIN}/figram", "check", p2])
        if c.returncode == 0:
            docs.append((f"colon-{seed}", p2, 20, 25))
        else:
            print("transformed doc rejected", p2, c.stdout[-300:], c.stderr[-300:], file=sys.stderr)
    return docs


class Session:
    def __init__(self):
        self.reqs = []  # (id, op, request dict)
        self.next = 1

    def add(self, op, **f):
        rid = self.next
        self.next += 1
        req = {"id": rid, "op": op}
        req.update(f)
        self.reqs.append((rid, op, req))
        return rid


def build(name, path, count, length, seed, rng):
    tr = run([f"{BIN}/figram", "trace", path, str(count), str(length), str(seed), "--directed"])
    if tr.returncode != 0:
        print("trace failed", name, tr.stderr[-300:], file=sys.stderr)
        return None
    s = Session()
    s.add("hello")
    states = []  # snapshots (dicts in engine order) seen in expectations
    for line in tr.stdout.decode().splitlines():
        t = json.loads(line)
        if t.get("start") is not None:
            st = t["start"]
            f = {}
            if "event" in st:
                f["event"] = st["event"]
                if st.get("guards"):
                    f["guards"] = st["guards"]
                    s.add("start", event=st["event"])  # without guards: need
            s.add("start", **f)
            snap = st["snapshot"]
        else:
            snap = t["from"]
        states.append(snap)
        for stp in t["steps"]:
            f = {"snapshot": snap, "event": stp["event"]}
            if "token" in stp:
                f["token"] = stp["token"]
            if stp.get("guards"):
                s.add("step", **f)  # without guards: need (or the same result)
                f["guards"] = stp["guards"]
            s.add("step", **f)
            ev = stp["event"]
            if ev.startswith("call:") and "token" in stp:
                tok = stp["token"]
                for bad in (tok - 1, 0, tok + 1):
                    if bad >= 0:
                        s.add("step", **dict(f, token=bad))
                s.add("step", **dict(f, snapshot=stp["snapshot"]))           # repeated delivery
                s.add("step", **dict(f, snapshot=dict(snap, at="__gone__")))  # recovered, accepted
                s.add("step", **dict(f, snapshot=dict(snap, at="__gone__"), token=tok + 1))  # recovered, stale
            if ev.startswith("timer:") and "token" in stp:
                s.add("step", **dict(f, token=stp["token"] + 1))
                s.add("step", **dict(f, snapshot=dict(snap, at="__gone__")))
            snap = stp["snapshot"]
            states.append(snap)
            if rng.random() < 0.05:
                s.add("restart", snapshot=snap)
            if rng.random() < 0.03:
                s.add("step", snapshot=dict(snap, at="__gone__"), event="btn:anything")
    # errors and edge requests
    some = states[rng.randrange(len(states))] if states else {"at": "x"}
    s.add("step", snapshot=dict(some, v=2), event="btn:x")                 # bad_snapshot
    s.add("step", snapshot=some, event="")                                  # bad_request
    s.add("step", snapshot=some, event="call:x")                            # bad_request
    s.add("step", snapshot=some, event="call:a:", token=1)                  # bad_request
    s.add("step", snapshot=some, event="btn:x", gaurds={})                 # bad_request
    s.add("start", event="timer:x")                                         # bad_request
    s.add("start", event="call:a:b")                                        # bad_request
    s.add("step", snapshot=dict(some, seq=MAXINT), event="btn:x")          # maybe seq_overflow
    s.add("restart", snapshot=dict(some, seq=MAXINT))                       # seq_overflow
    s.add("start", seq=MAXINT)                                              # seq_overflow
    for raw in RAW:                                                         # parse_error with id echo
        rid = s.next
        s.next += 1
        s.reqs.append((rid, "hello", raw.replace("%d", str(rid))))
    return s


RAW = ['{"id":%d,"op":"hello"} x', '{"id":%d,"op":"hello"', '{"id":%d,"op":"hello","op":"hello"}',
       '{"id":%d,"op":"step","snapshot":{"at":"a","seq":1e2000},"event":"btn:x"}', '{"id":%d,"op":"hello","x":"\udcff"}']


def engine(path, s, extra=()):
    data = "".join((r if isinstance(r, str) else dumps(r)) + "\n" for _, _, r in s.reqs).encode("utf-8", "surrogateescape")
    r = run([f"{BIN}/figram-engine", *extra, path], data, timeout=600)
    lines = r.stdout.decode().splitlines()
    if r.returncode != 0 or len(lines) != len(s.reqs):
        raise SystemExit(f"engine {path}: rc {r.returncode}, {len(lines)} replies for {len(s.reqs)}: {r.stderr[-500:]}")
    return lines


def too_large_session(path):
    """snapshot_too_large and too_large under --max-request (features: btn:ping waits for every pong)."""
    s = Session()
    for n in (300, 370, 380, 390):
        snap = {"at": "home", "pending": [{"call": "ping", "token": 9999 - i} for i in range(n)], "seq": 10000}
        s.add("step", snapshot=snap, event="btn:ping")
        s.add("step", snapshot=snap, event="call:ping:pong", token=9999 - n + 1)
        s.add("restart", snapshot=snap)
    s.add("step", snapshot=snap, event="btn:" + "x" * 20000)      # too_large
    return s


def main():
    rng = random.Random(7)
    valid = open(f"{OUT}/valid.jsonl", "w")
    n = 0
    for name, path, count, length in documents():
        s = build(name, path, count, length, 7, rng)
        if s is None:
            continue
        lines = engine(path, s)
        for (rid, op, req), line in zip(s.reqs, lines):
            rep = json.loads(line)
            assert rep.get("id") == rid, (name, rid, line)
            valid.write(dumps({"k": f"{name}/{rid}", "doc": path, "op": op, "id": rid,
                               "req": req if isinstance(req, dict) else {"raw_hex": req.encode("utf-8", "surrogateescape").hex()}, "line": line}) + "\n")
            n += 1
    feat = f"{FIG}/examples/features.bot.json"
    s = too_large_session(feat)
    lines = engine(feat, s, ("--max-request", "12000"))
    for (rid, op, req), line in zip(s.reqs, lines):
        valid.write(dumps({"k": f"limit/{rid}", "doc": feat, "op": op, "id": rid, "req": req, "line": line,
                           "max_request": 12000}) + "\n")
        n += 1
    valid.close()
    print("valid replies", n)


main()

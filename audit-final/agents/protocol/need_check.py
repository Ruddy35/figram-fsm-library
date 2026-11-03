"""For every recorded step/start request with guards: the request without guards answers `need`
(or a result); repeating it with only the needed values (taken from the original guards) must
give exactly the original reply (rule 3 of a step: need is sufficient, extra guards irrelevant).
usage: need_check.py REPO DUMP"""
import collections, json, os, subprocess, sys
repo, dump = sys.argv[1:3]
E = os.path.join(repo, ".lake/build/bin/figram-engine")
streams = collections.defaultdict(list)
for l in open(dump):
    r = json.loads(l)
    q = json.loads(r["req"])
    if q.get("op") in ("step", "start") and "guards" in q:
        streams[r["doc"]].append((q, json.loads(r["line"])))
bad = total = outcomes = 0
for doc, items in streams.items():
    p = subprocess.Popen([E, doc], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    def ask(q):
        p.stdin.write((json.dumps(q) + "\n").encode()); p.stdin.flush()
        return json.loads(p.stdout.readline())
    for q, orig in items:
        total += 1
        outcomes += q.get("event", "").startswith("call:")
        g = q.pop("guards")
        r = ask(q)
        rounds = 0
        while "need" in r and rounds < 3:
            rounds += 1
            q.setdefault("guards", {}).update({n: g[n] for n in r["need"]})
            r = ask(q)
        r["id"] = orig["id"]
        if r != orig:
            bad += 1
            if bad < 5: print("MISMATCH", q, r, orig)
        elif rounds > 1:
            bad += 1; print("need asked twice", q)
    p.stdin.close(); p.wait(10)
print(f"need sufficiency: requests {total} (outcome events {outcomes}), mismatches {bad}")
sys.exit(1 if bad else 0)

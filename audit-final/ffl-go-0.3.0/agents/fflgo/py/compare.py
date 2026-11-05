"""compare.py PREFIX CORPUS: join the three verdict files; group the disagreements by mutation."""
import collections, json, sys
P, C = sys.argv[1], sys.argv[2]
def load(s):
    d = {}
    for l in open(f"{P}.{s}.tsv"):
        k, v = l.rstrip("\n").split("\t", 1)
        d[k] = v
    return d
f, g, p = load("fflgo"), load("refgo"), load("py")
assert len(f) == len(g) == len(p) and len(f) > 0, (len(f), len(g), len(p))
lines = {}
for l in open(C):
    e = json.loads(l)
    lines[e["k"]] = e
total = collections.Counter(); diff = collections.defaultdict(list); verdicts = collections.Counter()
for k in f:
    e = lines[k]
    mut = e.get("mut", "valid")
    trip = (f[k], g[k], p[k])
    verdicts[trip] += 1
    total[mut] += 1
    if not (f[k] == g[k] == p[k]):
        diff[(mut, trip)].append(k)
print("cases", len(f))
print("verdict triples (fflgo, refgo, python):")
for t, n in verdicts.most_common():
    print(f"  {n:7d} {t}")
print("disagreements by mutation (fflgo, refgo, python):", sum(len(v) for v in diff.values()))
for (mut, trip), ks in sorted(diff.items(), key=lambda x: (x[0][0], str(x[0][1]))):
    ex = lines[ks[0]]
    print(f"  {mut:28s} {str(trip):40s} n={len(ks):6d} of {total[mut]:6d}  e.g. op={ex['op']} id={ex['id']} {ex['line'][:150]}")

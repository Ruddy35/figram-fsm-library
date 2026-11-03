"""The same request streams through figram-engine and through the C ABI (capi/linesrv):
byte-identical replies expected. usage: parity.py REPO DUMP LINESRV"""
import collections, json, subprocess, sys
repo, dump, srv = sys.argv[1:4]
streams = collections.defaultdict(list)
for l in open(dump):
    r = json.loads(l); streams[r["doc"]].append((r["req"], r["line"]))
total = diff = 0
for doc, items in streams.items():
    data = "".join(q + "\n" for q, _ in items).encode()
    out = subprocess.run([srv, doc], input=data, capture_output=True, timeout=300, cwd=repo).stdout.decode().splitlines()
    for (q, a), b in zip(items, out):
        total += 1
        if a.rstrip("\n") != b:
            diff += 1
            if diff < 5: print("DIFF", q[:200], "\n process:", a[:200], "\n library:", b[:200])
    if len(out) != len(items):
        diff += 1; print("count mismatch", doc, len(out), len(items))
print(f"parity: documents {len(streams)}, requests {total}, differences {diff}")
sys.exit(1 if diff else 0)

"""Copy the fix wave's reports, briefs, scripts and text logs into audit-final/fixes (no ELF, gzip > 1 MiB)."""
import gzip, os, shutil, sys

SRC = "/home/user/figram-audit"
DST = "/home/user/figram-fsm-library/audit-final/fixes"
TEXT_EXT = (".log", ".stdout", ".stderr", ".txt", ".out", ".jsonl", ".json", ".md", ".csv", ".err", ".tsv")
if os.path.isdir(DST):
    shutil.rmtree(DST)
os.makedirs(DST)
for f in ("FIX_BRIEF.md", "PROTOCOL_CHANGES.md", "INTEGRATION_BRIEF.md"):
    shutil.copy2(os.path.join(SRC, f), os.path.join(DST, f))
n = gz = skipped = 0
for agent in sorted(os.listdir(os.path.join(SRC, "fixes"))):
    a = os.path.join(SRC, "fixes", agent)
    out = os.path.join(DST, agent)
    os.makedirs(out, exist_ok=True)
    for name in os.listdir(a):                       # top-level: report and scripts
        p = os.path.join(a, name)
        if os.path.isfile(p) and (name.endswith((".md", ".py", ".sh")) or name in ("COMMIT",)):
            shutil.copy2(p, os.path.join(out, name)); n += 1
    logs = os.path.join(a, "LOGS")
    for root, _, names in os.walk(logs):
        for name in names:
            p = os.path.join(root, name)
            with open(p, "rb") as fh:
                head = fh.read(4)
            if head == b"\x7fELF" or not (name.endswith(TEXT_EXT) or name == "COMMIT" or "." not in name):
                skipped += 1; continue
            rel = os.path.relpath(p, a)
            q = os.path.join(out, rel)
            os.makedirs(os.path.dirname(q), exist_ok=True)
            if os.path.getsize(p) > (1 << 20):
                with open(p, "rb") as fi, open(q + ".gz", "wb") as raw, \
                        gzip.GzipFile(filename="", mode="wb", compresslevel=9, fileobj=raw, mtime=0) as fo:
                    shutil.copyfileobj(fi, fo)
                gz += 1
            else:
                shutil.copy2(p, q)
            n += 1
print(f"copied={n} gzipped={gz} skipped={skipped}")

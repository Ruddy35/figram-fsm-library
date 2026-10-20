"""Extract the text after '=== REPORT.md ===' from a subagent JSONL transcript; write it to OUT.
Prints only sizes/line counts, never the content."""
import json, sys
src, out = sys.argv[1], sys.argv[2]
best = None
def walk(o):
    global best
    if isinstance(o, str):
        if "\n=== REPORT.md ===" in o and (best is None or len(o) > len(best)):
            best = o
    elif isinstance(o, dict):
        for v in o.values(): walk(v)
    elif isinstance(o, list):
        for v in o: walk(v)
with open(src, encoding="utf-8") as f:
    for line in f:
        try: walk(json.loads(line))
        except ValueError: pass
if best is None:
    print("NOT FOUND"); sys.exit(1)
text = best.rsplit("\n=== REPORT.md ===", 1)[1].lstrip("\n")
open(out, "w", encoding="utf-8").write(text if text.endswith("\n") else text + "\n")
print(f"written {out}: {len(text)} chars, {text.count(chr(10))} lines; first line: {text.splitlines()[0][:80]!r}")

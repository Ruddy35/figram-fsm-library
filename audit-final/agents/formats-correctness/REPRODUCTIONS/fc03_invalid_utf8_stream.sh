#!/usr/bin/env bash
# FC-03: on stdin (figram json, figram-engine) invalid UTF-8 is silently replaced by U+FFFD,
# while files (figram check/fmt, engine document load) are rejected. Different byte strings alias.
F="${1:-/home/user/figram-audit-worktrees/formats-correctness/bin/figram}"
E="${2:-/home/user/figram-audit-worktrees/formats-correctness/bin/figram-engine}"
d=$(mktemp -d); trap 'rm -rf "$d"' EXIT
echo "== stream: figram json"; printf '"\xff"\n"\xed\xa0\x80"\n{"\xff":1,"\xfe":2}\n' | "$F" json
echo "== file: figram check";  printf '{"figram":1,"bot":{"initial":"\xff","children":{"\xff":{}}}}' > "$d/bad.json"; "$F" check "$d/bad.json"; echo "rc=$?"
echo "== engine: event \"\\xff\" fires the handler of event \"\\ufffd\""
printf '{"figram":1,"bot":{"initial":"a","children":{"a":{"on":{"\xef\xbf\xbd":[{"to":"b"}]}},"b":{}}}}' > "$d/doc.json"
printf '{"id":1,"op":"step","snapshot":{"at":"a"},"event":"\xff"}\n{"id":2,"op":"step","snapshot":{"at":"a"},"event":"\xfe"}\n' | "$E" "$d/doc.json"
python3 -c 'import json
try: json.loads(b"\"\xff\""); print("python: accepted")
except Exception as e: print("python: rejected:", type(e).__name__)'

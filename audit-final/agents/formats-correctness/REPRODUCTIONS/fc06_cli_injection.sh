#!/usr/bin/env bash
# FC-06: figram check / fmt / figram-engine print decoded names raw: LF forges lines,
# ESC sequences reach the terminal/CI log, NUL bytes pass through.
F="${1:-/home/user/figram-audit-worktrees/formats-correctness/bin/figram}"
E="${2:-/home/user/figram-audit-worktrees/formats-correctness/bin/figram-engine}"
d=$(mktemp -d); trap 'rm -rf "$d"' EXIT
printf '{"figram":1,"bot":{"initial":"a","children":{"a":{"on":{"btn:x":[{"to":"zz\\nok\\n"}]}}}}}' > "$d/lf.json"
echo "== figram check output lines:"; "$F" check "$d/lf.json" | cat -A; echo "rc=${PIPESTATUS[0]}"
if "$F" check "$d/lf.json" | grep -qx ok; then echo "!! 'figram check f | grep -qx ok' succeeds although check failed"; fi
printf '{"figram":1,"bot":{"initial":"a","children":{"a":{"on":{"btn:x":[{"to":"x\\u001b[2K\\u001b[1A\\u001b[32mok\\u001b[0m"}]}}}}}' > "$d/esc.json"
echo "== ESC bytes in check stdout:"; "$F" check "$d/esc.json" | od -c | head -4
printf '{"figram":1,"bot":{"initial":"a","children":{"a":{"on":{"btn:x":[{"to":"a\\u0000HIDDEN"}]}}}}}' > "$d/nul.json"
echo "== NUL in check stdout and engine stderr (Lean keeps the tail after NUL):"
"$F" check "$d/nul.json" | od -c | sed -n 2,4p; "$E" "$d/nul.json" </dev/null 2>&1 | od -c | sed -n 3,5p
echo "== parse error with raw ESC from the input file:"; printf '{"figram":1,"bot":\x1b}' > "$d/raw.json"; "$F" check "$d/raw.json" 2>&1 | od -c | tail -3

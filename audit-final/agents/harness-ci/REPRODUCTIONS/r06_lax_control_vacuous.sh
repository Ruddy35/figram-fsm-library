#!/usr/bin/env bash
# R06: faithful extract of verify.sh:118-131 (sensitivity control of json_fuzz.py).
# The control treats ANY non-zero exit of json_fuzz.py as "lax parser detected".
# Here lax.py cannot even be executed (as on a noexec /tmp): json_fuzz.py dies with a
# Traceback (PermissionError), yet the control prints "обнаружен".
# Usage: r06_lax_control_vacuous.sh <repo-root>
set -euo pipefail
P=${1:?repo}/ffi/python-example
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
cat > "$T/lax.py" <<'PY'
#!/usr/bin/env python3
import json, sys
for line in sys.stdin.buffer:
    try:
        print("OK " + json.dumps(json.loads(line.decode().rstrip("\n"))), flush=True)
    except Exception:
        print("ERR x", flush=True)
PY
chmod -x "$T/lax.py"            # <-- injected environment fault: not executable
if python3 $P/json_fuzz.py "$T/lax.py" 3000 1 >"$T/out" 2>&1; then
  echo "НЕТ: фаззер не заметил парсер без ограничений"; exit 1
else echo "контроль: парсер без ограничений обнаружен"; fi
echo "--- what json_fuzz.py actually printed:"; tail -2 "$T/out"

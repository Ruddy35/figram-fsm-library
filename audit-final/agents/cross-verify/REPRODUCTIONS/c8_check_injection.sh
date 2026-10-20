#!/usr/bin/env bash
# C8 (cross-verify, independent): `figram check` echoes document names unescaped.
# Usage: c8_check_injection.sh FIGRAM_BINARY FIGRAM_ENGINE_BINARY SCRATCH_DIR
set -u
F=${1:?figram}; E=${2:?figram-engine}; S=${3:?scratch}
mkdir -p "$S"
# 1) A broken document: arrow target "x\nok\ny" does not exist -> check must fail.
cat > "$S/nl.bot.json" <<'JSON'
{"figram":1,"bot":{"initial":"a","children":{"a":{"on":{"btn:go":[{"to":"x\nok\ny"}]}}}}}
JSON
# 2) Same with an ESC sequence (OSC title set + clear screen) in a node-name position.
cat > "$S/esc.bot.json" <<'JSON'
{"figram":1,"bot":{"initial":"a","children":{"a":{"on":{"btn:go":[{"to":"\u001b]0;PWNED\u0007\u001b[2J"}]}}}}}
JSON
echo "== figram check nl.bot.json (exit code and raw stdout)"
"$F" check "$S/nl.bot.json" > "$S/nl.out" 2> "$S/nl.err"; echo "exit_code=$?"
echo "-- stdout lines:"; cat -A "$S/nl.out"; echo "-- stderr:"; cat "$S/nl.err"
echo "== the documented-looking idiom: figram check f | grep -qx ok"
if "$F" check "$S/nl.bot.json" 2>/dev/null | grep -qx ok; then
  echo "IDIOM SAYS OK (document is actually invalid)  -> INJECTION REPRODUCED"
else
  echo "idiom says not ok"
fi
echo "== same idiom with set -o pipefail"
( set -o pipefail; if "$F" check "$S/nl.bot.json" 2>/dev/null | grep -qx ok; then echo "pipefail: OK (bad)"; else echo "pipefail: not ok (exit code wins), rc=$?"; fi )
echo "== control: valid pizza document prints exactly 'ok'"
"$F" check "$(dirname "$0")/../examples/pizza.bot.json"; echo "exit_code=$?"
echo "== figram check esc.bot.json: raw bytes of stdout (od -c, ESC = 033)"
"$F" check "$S/esc.bot.json" > "$S/esc.out" 2>/dev/null; echo "exit_code=$?"
od -c "$S/esc.out" | head -8
grep -c $'\x1b' "$S/esc.out" | sed 's/^/lines containing raw ESC: /'
echo "== figram-engine prints the same names to stderr on load failure"
"$E" "$S/esc.bot.json" < /dev/null > /dev/null 2> "$S/esc.engine.err"; echo "exit_code=$?"
od -c "$S/esc.engine.err" | head -6
grep -c $'\x1b' "$S/esc.engine.err" | sed 's/^/stderr lines containing raw ESC: /'

#!/usr/bin/env bash
# R13: zero-case / partial-input behaviour of the Python harness (baseline versions).
# Each line must report a NON-zero exit. Usage: r13_zero_cases.sh <repo-root>
R=${1:?repo}; P=$R/ffi/python-example; E=$R/bin/figram-engine; F=$R/bin/figram; D=$(mktemp -d); trap 'rm -rf "$D"' EXIT
: > "$D/empty.jsonl"; echo '{"from":{"at":"menu"},"steps":[]}' > "$D/nosteps.jsonl"
$F trace "$R/examples/pizza.bot.json" 5 5 1 > "$D/t.jsonl" 2>/dev/null
run() { "$@" > "$D/out" 2>&1; echo "exit=$? :: $(tail -1 "$D/out" | cut -c1-100) :: ${*: -3}"; }
run python3 $P/conformance.py $E "$R/examples/pizza.bot.json=$D/empty.jsonl"
run python3 $P/conformance.py $E "$R/examples/pizza.bot.json=$D/nosteps.jsonl"
run python3 $P/conformance.py $E
run python3 $P/random_docs.py $F $E 0 12
run python3 $P/json_fuzz.py $F 0 1
run python3 $P/loadtest.py $E "$R/examples/pizza.bot.json" "$D/t.jsonl" 0 1
run python3 $P/loadtest.py $E "$R/examples/pizza.bot.json" "$D/empty.jsonl" 4 1

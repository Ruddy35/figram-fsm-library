#!/usr/bin/env bash
# R03: real verify-prebuilt.sh (baseline) with examples/golden.jsonl truncated to 2 of 12 scenarios
# (timer_tokens, recover_deleted_current, nested_resume … removed) ends "ПРОЙДЕНА" with exit 0.
# The only trace is a missing "ok" line and a cmp message on stderr.
# Usage: r03_golden_truncated_green.sh [--quick]     (exit 0 here = defect reproduced)
set -u
W=$(mktemp -d); trap 'rm -rf "$W"' EXIT
git -C /home/user/figram-fsm-library archive audit/baseline | tar -x -C "$W"
# isolate from the unrelated client_contract.py flake (R02)
sed -i 's/    except FileNotFoundError:/    except (FileNotFoundError, ProcessLookupError):/' "$W/ffi/python-example/client_contract.py"
head -2 "$W/examples/golden.jsonl" > "$W/g" && mv "$W/g" "$W/examples/golden.jsonl"
(cd "$W" && ./verify-prebuilt.sh "$@"); rc=$?
echo "verify-prebuilt.sh exit code with truncated golden: $rc"
exit $rc

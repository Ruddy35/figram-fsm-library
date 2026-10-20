#!/usr/bin/env bash
# R05: per-constraint sensitivity of json_fuzz.py (verify.sh "Текстовый слой JSON").
# A model parser implementing the json_fuzz oracle exactly must give 0 mismatches (rc 0);
# dropping ANY single format constraint must be detected (rc 1). Exit 1 = some constraint undetected.
# Usage: run.sh <repo-root> [cases]
R=${1:?repo}; N=${2:-30000}; H=$(cd "$(dirname "$0")" && pwd); bad=0
export JSON_FUZZ_DIR="$R/ffi/python-example"
for relax in "" dup depth exp surrogate const; do
  out=$(FIGRAM_MODEL_RELAX=$relax timeout 600 python3 "$R/ffi/python-example/json_fuzz.py" "$H/model_parser.py" "$N" 1 2>&1); rc=$?
  echo "relax=[${relax:-none}] json_fuzz rc=$rc :: $(echo "$out" | grep -E '^случаев|Traceback|SystemExit' | head -1)"
  if [ -z "$relax" ]; then [ $rc -eq 0 ] || bad=1; else [ $rc -eq 1 ] || { bad=1; echo "   -> constraint '$relax' NOT detected by json_fuzz"; }; fi
done
exit $bad

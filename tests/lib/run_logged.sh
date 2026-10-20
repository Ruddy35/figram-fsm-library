#!/bin/sh
# fallback for run.sh outside the audit machine: run.sh LOGPATH CMD...
log=$1; shift; mkdir -p "$(dirname "$log")"
"$@" > "$log.stdout" 2> "$log.stderr"; rc=$?
{ echo "command: $*"; echo "exit_code: $rc"; tail -200 "$log.stdout"; tail -50 "$log.stderr"; } > "$log.log"
echo "exit_code=$rc log=$log.log"; exit $rc

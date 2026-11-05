#!/usr/bin/env bash
# run.sh LOGPATH CMD...  — runs CMD, records command, cwd, start/end time, exit code, stdout, stderr.
# LOGPATH without .log suffix; relative paths are resolved against $PWD. Prints exit code line at end.
set -u
log="$1"; shift
mkdir -p "$(dirname "$log")"
start=$(date -u +%FT%T.%3NZ)
"$@" > "$log.stdout" 2> "$log.stderr"
rc=$?
end=$(date -u +%FT%T.%3NZ)
{
  echo "command: $(printf '%q ' "$@")"
  echo "cwd: $PWD"
  echo "start: $start"
  echo "end: $end"
  echo "exit_code: $rc"
  echo "--- stdout ($(wc -c < "$log.stdout") bytes, last 200 lines)"
  tail -200 "$log.stdout"
  echo "--- stderr ($(wc -c < "$log.stderr") bytes, last 100 lines)"
  tail -100 "$log.stderr"
} > "$log.log"
echo "exit_code=$rc log=$log.log"
exit $rc

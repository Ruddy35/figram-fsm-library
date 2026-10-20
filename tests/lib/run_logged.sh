#!/bin/sh
# run_logged.sh LOGPATH CMD... — runs CMD and records the command, cwd, start/end time, exit code,
# stdout and stderr: LOGPATH.stdout, LOGPATH.stderr and a summary LOGPATH.log (tails of both).
# Prints "exit_code=N log=LOGPATH.log" and exits with the exit code of CMD.
log=$1; shift
mkdir -p "$(dirname "$log")"
start=$(date -u +%FT%TZ)
"$@" > "$log.stdout" 2> "$log.stderr"; rc=$?
end=$(date -u +%FT%TZ)
{
  echo "command: $*"
  echo "cwd: $PWD"
  echo "start: $start"
  echo "end: $end"
  echo "exit_code: $rc"
  echo "--- stdout ($(wc -c < "$log.stdout") bytes, last 200 lines)"; tail -200 "$log.stdout"
  echo "--- stderr ($(wc -c < "$log.stderr") bytes, last 100 lines)"; tail -100 "$log.stderr"
} > "$log.log"
echo "exit_code=$rc log=$log.log"
exit $rc

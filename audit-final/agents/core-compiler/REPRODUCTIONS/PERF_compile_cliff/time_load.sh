#!/bin/sh
# time_load.sh ENGINE DOC LIMIT : seconds until the engine answers hello
s=$(date +%s.%N); echo '{"id":1,"op":"hello"}' | timeout "$3" prlimit --as=4000000000 -- "$1" "$2"; rc=$?; e=$(date +%s.%N)
echo "rc=$rc seconds=$(echo "$e - $s" | bc)"

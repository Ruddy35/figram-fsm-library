#!/usr/bin/env bash
# Every mutant of the kinds detach-flip / wait-free / wait-swap offered by --mutants is refused by
# --mutate (specInRange expects a number after the kind), so the engine exits 2 before any request.
# usage: refused_mutants.sh ENGINE DOC...
E=$1; shift
for d in "$@"; do
  total=0; refused=0
  for m in $(FIGRAM_ALLOW_MUTANTS=1 "$E" --mutants "$d"); do
    total=$((total+1))
    out=$(echo '{"op":"hello","id":1}' | FIGRAM_ALLOW_MUTANTS=1 "$E" --mutate "$m" "$d" 2>&1); rc=$?
    if [ $rc != 0 ]; then refused=$((refused+1)); echo "REFUSED rc=$rc $m: $out"; fi
  done
  echo "$d: offered $total, refused by --mutate $refused"
done

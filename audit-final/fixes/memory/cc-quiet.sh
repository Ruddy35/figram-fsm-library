#!/bin/sh
# Локально (тулчейн Lean из исходников): leanc добавляет в флаги компиляции системные .so,
# и при -c gcc печатает «linker input file unused». Обёртка убирает .so при компиляции (-c).
compile=0
for a in "$@"; do [ "$a" = -c ] && compile=1; done
if [ $compile = 1 ]; then
  for a in "$@"; do
    shift
    case "$a" in *.so) ;; *) set -- "$@" "$a" ;; esac
  done
fi
exec gcc "$@"

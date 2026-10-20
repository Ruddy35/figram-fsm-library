#!/bin/sh
# Deliberately broken system under test for a_roundtrip.py: `fmt` turns every
# "resume": true into false (a printer bug).  Everything else is the real binary.
real="${FIGRAM_REAL:-$(dirname "$0")/../../bin/figram}"
if [ "$1" = fmt ]; then "$real" "$@" | sed 's/"resume": true/"resume": false/'; else exec "$real" "$@"; fi

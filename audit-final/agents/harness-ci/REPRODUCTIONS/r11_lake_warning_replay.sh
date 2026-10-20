#!/usr/bin/env bash
# R11: does `lake build` re-print a warning of an already-built module (Lake log replay)?
cd "$1"
echo "--- first build"; lake build 2>&1 | grep -iE "warning|Build completed|error" | head -5
echo "--- second build (nothing to rebuild)"; lake build 2>&1 | grep -iE "warning|Build completed|error|Replayed" | head -5

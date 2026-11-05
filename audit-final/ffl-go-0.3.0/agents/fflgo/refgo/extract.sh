#!/bin/sh
# Extracts the reference validator (lines 240-468 of clients/go/main.go) verbatim.
src=/home/user/figram-audit-worktrees/cleanup-int/clients/go/main.go
{ printf 'package main\n\nimport (\n\t"encoding/json"\n\t"fmt"\n\t"strconv"\n)\n\n'; sed -n 240,468p "$src"; } > ref_extracted.go

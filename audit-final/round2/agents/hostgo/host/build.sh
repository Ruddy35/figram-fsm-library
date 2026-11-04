#!/usr/bin/env bash
# Builds the host: the reference wrapper is copied from clients/go first (sync_wrapper.sh), the cgo
# flags come from the environment as docs/embedding.md prescribes (env.sh).
set -euo pipefail
cd "$(dirname "$0")"
./sync_wrapper.sh
source ./env.sh
go vet .
go build -o bin/hostgo .
echo "built bin/hostgo"
if [ "${RACE:-0}" = 1 ]; then
  go build -race -o bin/hostgo-race .
  echo "built bin/hostgo-race"
fi

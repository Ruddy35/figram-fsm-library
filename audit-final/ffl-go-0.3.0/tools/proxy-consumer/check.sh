#!/usr/bin/env bash
# Fetch ffl-go v0.3.0 through the Go module proxy into a fresh module with an empty module cache,
# build the README quick start and run it on pizza.bot.json of the Figram release.
set -euo pipefail
source /home/user/figram-audit/env.sh >/dev/null 2>&1
cd "$(dirname "$0")"
export GOMODCACHE="$PWD/modcache" GOFLAGS=-modcacherw GOPROXY=https://proxy.golang.org GOSUMDB=sum.golang.org
rm -f go.mod go.sum
go mod init example.com/consumer
go get github.com/Ruddy35/ffl-go@v0.3.0
grep ffl-go go.mod go.sum
go build -o consumer .
./consumer "$1"

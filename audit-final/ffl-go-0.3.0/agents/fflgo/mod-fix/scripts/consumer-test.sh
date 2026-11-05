#!/usr/bin/env bash
# Builds a program that uses this module the way a consumer does, from a separate module:
#   1. plain `go build` (the bundled libraries), then runs it;
#   2. `go mod vendor` + `go build -mod=vendor` (the libraries must be vendored);
#   3. `go build -ldflags=-extldflags=-Wl,--gc-sections` (prints both sizes);
#   4. with LIBFIGRAM=<extracted libfigram-<version>-linux-x86_64 directory>: the build tag
#      figram_system with pkg-config, and the program must not contain lib/linux_amd64.
#
#   scripts/consumer-test.sh [WORKDIR]     (default: a temporary directory, removed at exit)
set -euo pipefail
root=$(cd "$(dirname "$0")/.." && pwd)
if [ $# -gt 0 ]; then work=$1; mkdir -p "$work"; else work=$(mktemp -d); trap 'rm -rf "$work"' EXIT; fi
cd "$work"
cat > go.mod <<MOD
module example.com/consumer

go 1.23

require github.com/Ruddy35/ffl-go v0.0.0

replace github.com/Ruddy35/ffl-go => $root
MOD
cat > main.go <<'GO'
package main

import (
	"encoding/json"
	"fmt"
	"os"

	figram "github.com/Ruddy35/ffl-go"
)

func main() {
	doc, err := os.ReadFile(os.Args[1])
	if err != nil {
		panic(err)
	}
	bot, err := figram.Load(string(doc))
	if err != nil {
		panic(err)
	}
	defer bot.Close()
	res, err := bot.Start(0)
	if err != nil {
		panic(err)
	}
	if res, err = bot.Step(res.Snapshot, "btn:order", nil); err != nil {
		panic(err)
	}
	b, _ := json.Marshal(res)
	fmt.Println(string(b))
}
GO
want='{"actions":[{"send":"catalog"}],"snapshot":{"at":"catalog","back":"menu","resume":{"order":"catalog"},"seq":2}}'
check() { # binary
  got=$("./$1" "$root/testdata/pizza.bot.json")
  [ "$got" = "$want" ] || { echo "$1: unexpected output: $got"; exit 1; }
  echo "$1: ok, $(stat -c %s "$1") bytes"
}
go build -o plain . && check plain
go mod vendor
[ -f vendor/github.com/Ruddy35/ffl-go/lib/linux_amd64/libInit.a ] || { echo "go mod vendor did not copy the libraries"; exit 1; }
go build -mod=vendor -o vendored . && check vendored
rm -rf vendor
go build -ldflags=-extldflags=-Wl,--gc-sections -o gcsections . && check gcsections
if [ -n "${LIBFIGRAM:-}" ]; then
  PKG_CONFIG_PATH=$LIBFIGRAM/lib/pkgconfig go build -tags figram_system -x -o system . 2> build.log
  if grep -q "ffl-go/lib/linux_amd64" build.log; then echo "figram_system linked lib/linux_amd64"; exit 1; fi
  check system
fi
echo "consumer builds: ok"

#!/usr/bin/env bash
# Шаги Go из verify.sh на адаптированной сборке (ffi/out -> ffi/audit/out). Код 0 — всё зелёное/красное как надо.
#   ffi/audit/go_verify_block.sh TRACES_BIG TRACES_SMALL
set -u
cd "$(dirname "$0")/../go-example"
BIG=$1; SMALL=$2; D=../../examples/pizza.bot.json; rc=0
CC=gcc go build -o gofig . && ./gofig -rounds 3 -bots 4 $D "$BIG" || rc=1
CC=gcc go build -race -o gofig-race . && GOMAXPROCS=4 ./gofig-race -rounds 3 -bots 3 $D "$SMALL" || rc=1
for a in "-rounds 0" "-bots 0"; do ./gofig $a $D "$SMALL" >/dev/null 2>&1 && { echo "НЕТ: принято $a"; rc=1; }; done
for k in recovered-broken recovered-false no-id extra-error empty seq-bool null-snapshot; do
  ./gofig -corrupt $k $D "$SMALL" >/dev/null 2>&1 && { echo "НЕТ: не замечена порча $k"; rc=1; }
done
echo "порча и неверные аргументы: красные"
./gofig -reload 5000 $D "$SMALL" || rc=1
(head -1 "$SMALL"; echo '{oops') > /tmp/corrupt.$$.jsonl
./gofig $D /tmp/corrupt.$$.jsonl >/dev/null 2>&1 && { echo "НЕТ: повреждённый сценарий принят"; rc=1; }; rm -f /tmp/corrupt.$$.jsonl
echo "go_verify_block rc=$rc"; exit $rc

#!/bin/sh
# Ответы настоящего движка на строки, которые не разбираются на уровне текста
# (id:null), и на необычные значения, которые может выдать json.dumps.
#   sh probe_engine_replies.sh ENGINE DOC
E=$1; D=$2
printf '%s\n' \
'{"id":1,"op":"step","snapshot":{"at":"menu","at":"order"},"event":"btn:x"}' \
'{"id":2,"op":"step","snapshot":{"at":"menu","resume":{"1":"a","1":"b"}},"event":"btn:x"}' \
'{"id":3,"op":"step","snapshot":{"at":"menu"},"event":"btn:\ud800"}' \
'{"id":4,"op":"step","snapshot":{"at":"menu"},"event":"timer:t","token":18446744073709551616}' \
'{"id":5,"op":"step","snapshot":{"at":"menu"},"event":"timer:t","token":1.5}' \
'{"id":6,"op":"step","snapshot":{"at":"menu"},"event":"btn:x","guards":{"a":true,"a":false}}' \
'{"id":7,"op":"step","snapshot":{"at":"menu"},"event":"timer:t","token":-0.0}' \
'{"id":8,"op":"step","snapshot":{"at":"menu"},"event":"timer:t","token":1e+300}' \
'{"id":9,"op":"step","snapshot":{"at":"menu","timers":{"true":1,"true":2}},"event":"btn:x"}' \
'{"id":10,"op":"step","snapshot":{"at":"menu"},"event":"btn:\u0000"}' \
'{"id":11,"op":"step","snapshot":{"at":"menu"},"event":"btn: "}' \
"{\"id\":12,\"op\":\"step\",\"snapshot\":{\"at\":\"menu\"},\"event\":\"timer:t\",\"token\":1$(printf '0%.0s' $(seq 1 400))}" \
| "$E" "$D"

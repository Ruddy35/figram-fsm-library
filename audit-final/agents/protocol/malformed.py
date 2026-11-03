"""Malformed (and some valid edge) replies: both client validators must reject the malformed
ones and accept the valid ones. Writes a JSONL corpus for the Go validator and runs the Python
validators (protocol.check_reply and the client's own hello/error checks) itself.

usage: malformed.py REPO OUT.jsonl
"""
import json, os, sys

repo, out = sys.argv[1], sys.argv[2]
sys.path.insert(0, os.path.join(repo, "clients", "python"))
import protocol  # noqa
import figram_client  # noqa

R = []  # (name, op, line, want)


def add(name, op, line, want):
    R.append((name, op, line, want))


snap = '{"at":"placing","pending":[{"call":"create_order","token":6}],"seq":6}'
# valid edge cases (the engine never writes some of them, but the schema allows them)
add("ok-call-token-detached", "step", '{"id":1,"snapshot":%s,"actions":[{"call":"p","token":6,"detached":true}]}' % snap, "OK")
add("ok-pending-empty", "step", '{"id":1,"snapshot":{"at":"a","pending":[]},"actions":[]}', "OK")
add("ok-pending-dup", "step", '{"id":1,"snapshot":{"at":"a","pending":[{"call":"x","token":1},{"call":"x","token":1}]},"actions":[]}', "OK")
add("ok-recovered", "step", '{"id":1,"snapshot":{"at":"a"},"actions":[],"recovered":true}', "OK")
add("ok-need-start", "start", '{"id":1,"need":["c"]}', "OK")
add("ok-hello", "hello", '{"id":1,"protocol":3,"states":13}', "OK")
add("ok-seq0", "step", '{"id":1,"snapshot":{"at":"a","seq":0},"actions":[]}', "OK")
# malformed
bad = {
    "call-token-string": '{"id":1,"snapshot":%s,"actions":[{"call":"p","token":"6"}]}' % snap,
    "call-token-neg": '{"id":1,"snapshot":%s,"actions":[{"call":"p","token":-1}]}' % snap,
    "call-token-big": '{"id":1,"snapshot":%s,"actions":[{"call":"p","token":9007199254740992}]}' % snap,
    "call-token-float": '{"id":1,"snapshot":%s,"actions":[{"call":"p","token":6.0}]}' % snap,
    "call-token-null": '{"id":1,"snapshot":%s,"actions":[{"call":"p","token":null}]}' % snap,
    "call-detached-false": '{"id":1,"snapshot":%s,"actions":[{"call":"p","detached":false}]}' % snap,
    "call-detached-1": '{"id":1,"snapshot":%s,"actions":[{"call":"p","detached":1}]}' % snap,
    "call-detached-str": '{"id":1,"snapshot":%s,"actions":[{"call":"p","detached":"true"}]}' % snap,
    "call-extra": '{"id":1,"snapshot":%s,"actions":[{"call":"p","token":6,"outcome":"x"}]}' % snap,
    "call-empty": '{"id":1,"snapshot":%s,"actions":[{"call":"","token":6}]}' % snap,
    "timer-detached": '{"id":1,"snapshot":%s,"actions":[{"start_timer":"t","ms":1,"token":6,"detached":true}]}' % snap,
    "send-token": '{"id":1,"snapshot":%s,"actions":[{"send":"m","token":6}]}' % snap,
    "pending-object": '{"id":1,"snapshot":{"at":"a","pending":{"call":"x","token":1}},"actions":[]}',
    "pending-extra": '{"id":1,"snapshot":{"at":"a","pending":[{"call":"x","token":1,"outcome":"ok"}]},"actions":[]}',
    "pending-no-token": '{"id":1,"snapshot":{"at":"a","pending":[{"call":"x"}]},"actions":[]}',
    "pending-empty-call": '{"id":1,"snapshot":{"at":"a","pending":[{"call":"","token":1}]},"actions":[]}',
    "pending-token-bool": '{"id":1,"snapshot":{"at":"a","pending":[{"call":"x","token":true}]},"actions":[]}',
    "pending-token-neg": '{"id":1,"snapshot":{"at":"a","pending":[{"call":"x","token":-1}]},"actions":[]}',
    "pending-token-float": '{"id":1,"snapshot":{"at":"a","pending":[{"call":"x","token":1.5}]},"actions":[]}',
    "pending-entry-string": '{"id":1,"snapshot":{"at":"a","pending":["x"]},"actions":[]}',
    "pending-null": '{"id":1,"snapshot":{"at":"a","pending":null},"actions":[]}',
    "hello-protocol-2": '{"id":1,"protocol":2,"states":13}',
    "hello-protocol-4": '{"id":1,"protocol":4,"states":13}',
    "hello-protocol-str": '{"id":1,"protocol":"3","states":13}',
    "hello-protocol-float": '{"id":1,"protocol":3.0,"states":13}',
    "hello-states-neg": '{"id":1,"protocol":3,"states":-1}',
    "hello-states-big": '{"id":1,"protocol":3,"states":18446744073709551616}',
    "recovered-false": '{"id":1,"snapshot":{"at":"a"},"actions":[],"recovered":false}',
    "need-dup": '{"id":1,"need":["c","c"]}',
    "need-empty": '{"id":1,"need":[]}',
    "need-empty-name": '{"id":1,"need":[""]}',
    "snapshot-extra": '{"id":1,"snapshot":{"at":"a","x":1},"actions":[]}',
    "snapshot-v2": '{"id":1,"snapshot":{"at":"a","v":2},"actions":[]}',
    "dup-key-at": '{"id":1,"snapshot":{"at":"","at":"a"},"actions":[]}',
    "dup-key-token": '{"id":1,"snapshot":%s,"actions":[{"call":"p","token":-5,"token":6}]}' % snap,
    "error-unknown-code": '{"id":1,"error":"x","code":"oops"}',
    "error-extra": '{"id":1,"error":"x","code":"bad_request","snapshot":{}}',
    "id-float": '{"id":1.0,"snapshot":{"at":"a"},"actions":[]}',
}
for k, v in bad.items():
    add(k, "hello" if k.startswith("hello") else ("start" if k.startswith("need") else "step"), v, "ERR")

with open(out, "w") as f:
    for name, op, line, want in R:
        f.write(json.dumps({"op": op, "id": 1, "line": line, "want": want, "name": name}) + "\n")

mism = []
for name, op, line, want in R:
    try:
        resp = figram_client._decode(line.encode())
        if op == "hello":
            figram_client._check_hello(resp)
            protocol.check_reply(resp, 1, op)
        elif "error" in resp:
            figram_client._error_reply(resp)
            protocol.check_reply(resp, 1, op)
        else:
            protocol.check_reply(resp, 1, op)
        got = "OK"
    except (protocol.ProtocolError, ValueError) as e:
        got = "ERR"
    if got != want:
        mism.append(name)
        print(f"python validator MISMATCH {name}: want {want}, got {got}")
print(f"python validators: {len(R)} replies, mismatches {len(mism)}: {mism}")

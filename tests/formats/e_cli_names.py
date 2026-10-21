#!/usr/bin/env python3
"""(e) Decoded names in CLI diagnostics: output injection, NUL handling.

`figram check` prints its diagnostics with names from the document decoded
(\\u001b becomes a raw ESC byte, \\n a real line break, \\u0000 a NUL byte).
`figram fmt`/`check` parse errors and `figram-engine` document-load errors go
to stderr the same way.  The engine protocol replies are JSON-escaped.

Property P (expected of a safe CLI): stdout/stderr of `figram check`,
`figram fmt` and `figram-engine <doc>` contain no raw C0 control byte other
than the LF that ends each diagnostic line, and `check` never prints a line
that is exactly "ok" when it exits non-zero.

Rows marked FC-06 violate P on the baseline (commit d69bbaf, before the fix).
`--control` runs the same rows with harmless names: P must hold (shows the
detector is not trivially red).  `--strict` makes FC-06 rows count.

    python3 e_cli_names.py [--control] [--strict]
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import ENGINE, FIGRAM, run  # noqa: E402


def doc_with_target(name):
    return json.dumps({"figram": 1, "bot": {"initial": "a", "children": {
        "a": {"on": {"btn:x": [{"to": name}]}}}}})


def doc_with_cond(name):
    return json.dumps({"figram": 1, "bot": {"initial": "a", "children": {
        "a": {"on": {"btn:x": [{"if": name, "to": "a"}]}}}}})


def doc_dup_node(name):
    return json.dumps({"figram": 1, "bot": {"initial": "a", "children": {
        "a": {}, name: {}, "s": {"initial": name, "children": {name: {}}}}}})


ROWS = [
    # id, document text, harmless-name control text
    ("esc-in-target", doc_with_target("x\x1b[2K\x1b[1A\x1b[32mok\x1b[0m"), doc_with_target("x-plain")),
    ("lf-forges-ok-line", doc_with_target("zz\nok\n"), doc_with_target("zz ok")),
    ("nul-in-target", doc_with_target("a\x00HIDDEN"), doc_with_target("aHIDDEN")),
    ("quote-in-cond", doc_with_cond('c"; rm -rf "'), doc_with_cond("c-plain")),
    ("esc-in-dup-node", doc_dup_node("n\x1b]0;pwned\x07"), doc_dup_node("n-plain")),
    ("cr-in-target", doc_with_target("x\rok      "), doc_with_target("x ok")),
    # parse-level errors: raw text from the key / the input
    ("esc-in-dup-key", '{"figram":1,"x-\\u001b[31m":1,"x-\\u001b[31m":2,"bot":{}}',
     '{"figram":1,"x-r":1,"x-r":2,"bot":{}}'),
    ("raw-esc-top-level", '{"figram":1,"bot":\x1b[31m}', '{"figram":1,"bot":?}'),
    ("nl-in-dup-key", '{"figram":1,"a\\nok\\n":1,"a\\nok\\n":2,"bot":{}}', '{"figram":1,"a ok":1,"a ok":2,"bot":{}}'),
]


def raw_controls(b: bytes):
    return sorted({c for c in b if c < 0x20 and c != 0x0A} | ({0x7F} if 0x7F in b else set()))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--control", action="store_true")
    ap.add_argument("--strict", action="store_true")
    ap.add_argument("--tmp", default=os.environ.get("TMPDIR", "/tmp"))
    a = ap.parse_args()
    p = os.path.join(a.tmp, f"fc-e-{os.getpid()}.json")
    viol = checks = 0
    for rid, text, ctl in ROWS:
        t = ctl if a.control else text
        with open(p, "w", encoding="utf-8") as f:
            f.write(t)
        results = {
            "check.stdout": run([FIGRAM, "check", p]),
            "fmt.stderr": run([FIGRAM, "fmt", p]),
            "engine.stderr": run([ENGINE, p], b""),
        }
        rc_c, out_c, err_c = results["check.stdout"]
        streams = {
            "check.stdout": out_c, "check.stderr": err_c,
            "fmt.stderr": results["fmt.stderr"][2],
            "engine.stderr": results["engine.stderr"][2],
        }
        bad = []
        for name, data in streams.items():
            checks += 1
            rc = raw_controls(data)
            if rc:
                bad.append(f"{name}: raw bytes {['0x%02x' % c for c in rc]}")
        checks += 1
        if rc_c != 0 and any(line.strip() == b"ok" for line in (out_c + err_c).split(b"\n")):
            bad.append("check exited %d but printed a line 'ok'" % rc_c)
        checks += 1
        fmt_err = streams["fmt.stderr"]
        if results["fmt.stderr"][0] != 0 and fmt_err.count(b"\n") != 1:
            bad.append(f"fmt printed one error as {fmt_err.count(chr(10).encode())} lines")
        print(f"{'VIOL' if bad else 'ok  '} {rid:20s} check rc={rc_c} engine rc={results['engine.stderr'][0]}")
        for b in bad:
            print("       ", b)
        for name in ("check.stdout", "fmt.stderr", "engine.stderr"):
            data = streams[name]
            if data:
                print(f"        {name}: {data[:160]!r}")
        viol += bool(bad)
    # Lean-level string: fmt keeps the NUL and everything after it (no C truncation)
    with open(p, "w", encoding="utf-8") as f:
        f.write(doc_with_target("a\x00HIDDEN"))
    rc, out, _ = run([FIGRAM, "fmt", p])
    lean_nul_ok = rc == 0 and b'"to": "a\\u0000HIDDEN"' in out
    print(f"fmt keeps NUL as \\u0000 and the tail after it: {lean_nul_ok}")
    # Engine protocol replies are JSON-escaped (contrast)
    e_doc = os.path.join(a.tmp, f"fc-e-{os.getpid()}-ok.json")
    with open(e_doc, "w") as f:
        f.write(json.dumps({"figram": 1, "bot": {"initial": "a", "children": {"a": {}}}}))
    req = '{"id":1,"op":"step","snapshot":{"at":"a"},"event":"e","guards":{"\\u001b[31m\\n":1}}\n'
    rc, out, _ = run([ENGINE, e_doc], req.encode())
    proto_ok = rc == 0 and not raw_controls(out[:-1]) and b"\\u001b" in out
    print(f"engine protocol reply escaped: {proto_ok} {out[:120]!r}")
    os.unlink(p)
    os.unlink(e_doc)
    print(f"rows={len(ROWS)} stream-checks={checks} rows-violating-P={viol} control={a.control}")
    if a.control:
        sys.exit(1 if viol else 0)
    sys.exit(1 if (a.strict and viol) or not lean_nul_ok or not proto_ok else 0)


if __name__ == "__main__":
    main()

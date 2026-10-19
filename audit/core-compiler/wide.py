#!/usr/bin/env python3
"""Very wide documents: does load fail in a CONTROLLED way (error message,
exit 1) or crash (signal / stack overflow) or hang?

  python3 wide.py --engine BIN --figram BIN [--sizes 50000,100000,...] [--timeout S]

shape `msgs`:   one section with N empty messages (no arrows)
shape `arrows`: one message with N distinct events (one long `on` list)
shape `variants`: one message, one event with N variants
"""

import argparse
import json
import os
import subprocess
import tempfile
import time


def doc(shape, n):
    if shape == "msgs":
        ch = ",".join(f'"m{i}":{{}}' for i in range(n))
        return '{"figram":1,"bot":{"initial":"m0","children":{' + ch + '}}}'
    if shape == "arrows":
        on = ",".join(f'"btn:{i}":[{{"to":"a"}}]' for i in range(n))
        return '{"figram":1,"bot":{"initial":"a","children":{"a":{"on":{' + on + '}}}}}'
    if shape == "variants":
        vs = ",".join('{"to":"a"}' for _ in range(n))
        return '{"figram":1,"bot":{"initial":"a","children":{"a":{"on":{"btn:x":[' + vs + ']}}}}}'
    raise ValueError(shape)


def run(cmd, inp, timeout):
    t0 = time.time()
    try:
        p = subprocess.run(["prlimit", "--as=4000000000", "--"] + cmd, input=inp, capture_output=True,
                           text=True, timeout=timeout)
        return {"rc": p.returncode, "s": round(time.time() - t0, 2),
                "stdout": p.stdout[:200], "stderr": p.stderr[-300:]}
    except subprocess.TimeoutExpired:
        return {"rc": "TIMEOUT", "s": timeout}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", required=True)
    ap.add_argument("--figram", required=True)
    ap.add_argument("--shapes", default="msgs,arrows,variants")
    ap.add_argument("--sizes", default="10000,50000,200000")
    ap.add_argument("--timeout", type=float, default=120)
    args = ap.parse_args()
    for shape in args.shapes.split(","):
        for n in map(int, args.sizes.split(",")):
            text = doc(shape, n)
            with tempfile.NamedTemporaryFile("w", suffix=".bot.json", delete=False) as f:
                f.write(text)
            e = run([args.engine, f.name], '{"id":1,"op":"hello"}\n', args.timeout)
            c = run([args.figram, "check", f.name], "", args.timeout)
            os.unlink(f.name)
            print(json.dumps({"shape": shape, "n": n, "bytes": len(text), "engine": e, "check": c},
                             ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()

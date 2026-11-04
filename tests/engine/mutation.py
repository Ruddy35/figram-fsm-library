"""Mutation testing of the scenario set.

For every document takes all mutations of the engine (`figram-engine --mutants`)
and checks that the scenario set detects each one. The test mode of the engine
is enabled by the environment variable FIGRAM_ALLOW_MUTANTS=1 (the test sets it
itself).

    python3 mutation.py ENGINE --golden examples/golden.jsonl DOC=TRACES.jsonl ...
"""

import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "clients", "python"))
from conformance import corpus, run
from protocol import default_timeout, mutant_env

MIN_MUTANTS = 1   # per document; an empty list of mutations is a failure, not 100%


def mutant_runs(engine, doc, m):
    """The engine runs mutation m: `hello` succeeds and names the mutation."""
    try:
        out = subprocess.run([engine, "--mutate", m, doc], input=b'{"id":1,"op":"hello"}\n',
                             capture_output=True, timeout=default_timeout(), env=mutant_env())
        reply = json.loads(out.stdout.decode().splitlines()[0])
    except (subprocess.TimeoutExpired, IndexError, ValueError):
        return False
    return out.returncode == 0 and reply.get("mutant") == m


def main():
    engine = sys.argv[1]
    items = corpus(sys.argv[2:])
    merged = {}
    for doc, traces in items:
        merged.setdefault(doc, []).extend(traces)
    killed = survived = 0
    survivors = []
    for doc, traces in merged.items():
        mutants = subprocess.run([engine, "--mutants", doc], capture_output=True, text=True,
                                 check=True, timeout=default_timeout(), env=mutant_env()).stdout.split()
        if len(mutants) < MIN_MUTANTS:
            print(f"{doc}: {len(mutants)} mutations, below the minimum {MIN_MUTANTS}")
            sys.exit(1)
        n, base = run(engine, doc, traces)
        if base or n == 0:
            print(f"the unmodified engine fails the set for {doc}: {base[:1]}")
            sys.exit(1)
        for m in mutants:
            # A mutation the engine refuses (exit 2) would fail every scenario and count as
            # detected; the engine must accept it and report it in `hello`.
            if not mutant_runs(engine, doc, m):
                print(f"{doc}: the engine refuses the mutation {m}: a harness error, not a detection")
                sys.exit(1)
            _, bad = run(engine, doc, traces, m)
            if bad:
                killed += 1
            else:
                survived += 1
                survivors.append(f"{doc}: {m}")
        print(f"{doc}: mutations {len(mutants)}")
    total = killed + survived
    print(f"detected {killed} of {total} mutations ({100 * killed // max(total, 1)}%)")
    for s in survivors:
        print("   survived:", s)
    sys.exit(0 if survived == 0 and total > 0 else 1)


if __name__ == "__main__":
    main()

"""Mutation testing of the scenario set.

For every document takes all mutations of the engine (`figram-engine --mutants`)
and checks that the scenario set detects each one. The test mode of the engine
is enabled by the environment variable FIGRAM_ALLOW_MUTANTS=1 (the test sets it
itself).

    python3 mutation.py ENGINE [--unreachable DOC=COVERAGE] --golden examples/golden.jsonl DOC=TRACES.jsonl ...

--unreachable takes the stderr of `figram trace DOC … --directed`: the arrow variants that no
reachable snapshot fires (the search went through all reachable places) with their numbers in
the compiled machine. A mutation of such a branch (`redirect`, `reverse-actions`, `drop-guard`,
`stay-reenter`) or of a row of only such branches (`swap-branches`) does not change the
behaviour: it is run too and must survive (if the set detects it, the variant was reached and the
coverage report is wrong), and it is counted apart from the others.
"""

import json
import os
import re
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


BRANCH_KINDS = ("redirect", "reverse-actions", "drop-guard", "stay-reenter")


def unreachable(path):
    """The machine branches and rows the coverage report of `figram trace` names unreachable."""
    text = open(path, encoding="utf-8").read()
    m = re.search(r"^arrow variants unreachable: (\d+)$", text, re.M)
    if not m:
        raise SystemExit(f"{path}: no line 'arrow variants unreachable: N' (the search did not finish)")
    branches = {int(x) for x in re.findall(r"^unreachable variant: \S+ branch (\d+)$", text, re.M)}
    rows = {int(x) for x in re.findall(r"^unreachable row: \S+ row (\d+)$", text, re.M)}
    if len(branches) != int(m.group(1)):
        raise SystemExit(f"{path}: {m.group(1)} unreachable variants announced, {len(branches)} listed")
    return branches, rows


def equivalent(m, dead):
    kind, _, arg = m.partition(":")
    if not arg.isdigit():
        return False
    branches, rows = dead
    return int(arg) in branches if kind in BRANCH_KINDS else kind == "swap-branches" and int(arg) in rows


def main():
    engine = sys.argv[1]
    args = sys.argv[2:]
    dead = {}
    while args and args[0] == "--unreachable":
        doc, cov = args[1].split("=", 1)
        dead[doc] = unreachable(cov)
        args = args[2:]
    items = corpus(args)
    merged = {}
    for doc, traces in items:
        merged.setdefault(doc, []).extend(traces)
    killed = survived = equiv = 0
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
            if equivalent(m, dead.get(doc, (set(), set()))):
                if bad:
                    print(f"{doc}: the mutation {m} of an unreachable variant is detected: "
                          "the variant was reached, the coverage report is wrong")
                    sys.exit(1)
                equiv += 1
            elif bad:
                killed += 1
            else:
                survived += 1
                survivors.append(f"{doc}: {m}")
        print(f"{doc}: mutations {len(mutants)}")
    total = killed + survived
    print(f"detected {killed} of {total} mutations ({100 * killed // max(total, 1)}%)")
    print(f"mutations of unreachable variants (not counted, all survived): {equiv}")
    for s in survivors:
        print("   survived:", s)
    sys.exit(0 if survived == 0 and total > 0 else 1)


if __name__ == "__main__":
    main()

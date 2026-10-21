"""Checks the engine on reference scenarios (in batch, one process per document).

Every step of a scenario becomes protocol requests; if the step needs
conditions, the `need` reply is checked first, then the result with the
conditions. The snapshot, the actions, the recovery flag and the list of
conditions are compared.

    python3 conformance.py ENGINE [--mutate M] DOC=TRACES.jsonl [DOC=TRACES ...]
    python3 conformance.py ENGINE [--mutate M] --golden examples/golden.jsonl

Exit status 0: no mismatches.
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "clients", "python"))
from protocol import ChildTimeout, ProtocolError, check_reply, default_timeout, mutant_env, run_child


def plan(trace):
    """(request, expectation) pairs for one scenario."""
    pairs = []
    if "from" in trace:
        snap = trace["from"]
    else:
        pairs.append(({"op": "start"}, {"result": trace["start"]}))
        snap = trace["start"]["snapshot"]
    for st in trace["steps"]:
        req = {"op": "step", "snapshot": snap, "event": st["event"]}
        if "token" in st:
            req["token"] = st["token"]
        exp = {"result": {"snapshot": st["snapshot"], "actions": st["actions"],
                          "recovered": st.get("recovered", False)}}
        if st["guards"]:
            pairs.append((req, {"need": sorted(st["guards"])}))
            pairs.append(({**req, "guards": st["guards"]}, exp))
        else:
            pairs.append((req, exp))
        snap = st["snapshot"]
    return pairs


def expected_reply(exp, rid, op):
    """The expectation from the scenario must conform to the protocol too."""
    if "need" in exp:
        return check_reply({"id": rid, "need": exp["need"]}, rid, op)
    e = exp["result"]
    reply = {"id": rid, "snapshot": e["snapshot"], "actions": e["actions"]}
    if e.get("recovered"):
        reply["recovered"] = True
    return check_reply(reply, rid, op)


def run(engine, doc, traces, mutate=None, timeout=None):
    """Run the scenarios of one document. Returns (requests, errors)."""
    if not traces:
        return 0, [f"{doc}: no scenarios"]
    pairs = [p for t in traces for p in plan(t)]
    if not pairs:
        return 0, [f"{doc}: the scenarios contain no request"]
    cmd = [engine] + (["--mutate", mutate] if mutate else []) + [doc]
    lines = [json.dumps({**req, "id": i}, ensure_ascii=False) for i, (req, _) in enumerate(pairs)]
    try:
        out = run_child(cmd, ("\n".join(lines) + "\n").encode(), timeout or default_timeout(),
                        f"conformance {os.path.basename(doc)}", env=mutant_env() if mutate else None)
    except ChildTimeout as e:
        return len(pairs), [str(e)]
    if out.returncode != 0:
        return len(pairs), [f"engine exited with code {out.returncode}: {out.stderr.decode()[:200]}"]
    raw = out.stdout.decode().split("\n")
    if raw and raw[-1] == "":
        raw.pop()
    bad = []
    if len(raw) != len(pairs):
        bad.append(f"replies {len(raw)} of {len(pairs)}")
    for i, ((req, exp), line) in enumerate(zip(pairs, raw)):
        try:
            expected_reply(exp, i, req["op"])
        except ProtocolError as e:
            bad.append(f"scenario: expectation {i} violates the protocol: {e}")
            continue
        try:
            resp = json.loads(line)
            variant = check_reply(resp, i, req["op"])
        except (ValueError, ProtocolError) as e:
            bad.append(f"reply {i}: {e}: {line[:200]}")
            continue
        if "need" in exp:
            ok = variant == "need" and sorted(resp["need"]) == exp["need"]
        else:
            e = exp["result"]
            ok = (variant == "result" and resp["snapshot"] == e["snapshot"]
                  and resp["actions"] == e["actions"]
                  and resp.get("recovered", False) is e.get("recovered", False))
        if not ok:
            bad.append(f"{req} → {resp}, expected {exp}")
    return len(pairs), bad


def load_jsonl(path):
    return [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]


def corpus(args):
    """List of (document, scenarios) from the arguments. An empty corpus is an error."""
    items = []
    it = iter(args)
    for a in it:
        if a == "--golden":
            path = next(it)
            base = os.path.dirname(path)
            by_doc = {}
            for t in load_jsonl(path):
                by_doc.setdefault(os.path.join(base, t["doc"]), []).append(t)
            items += list(by_doc.items())
        else:
            doc, traces = a.split("=", 1)
            items.append((doc, load_jsonl(traces)))
    if not items:
        raise SystemExit("empty corpus: no set of scenarios given")
    for doc, traces in items:
        if not traces:
            raise SystemExit(f"empty corpus for {doc}")
    return items


def main():
    args = sys.argv[1:]
    engine = args.pop(0)
    mutate = None
    if args and args[0] == "--mutate":
        mutate = args[1]
        args = args[2:]
    total = 0
    failures = []
    for doc, traces in corpus(args):
        n, bad = run(engine, doc, traces, mutate)
        total += n
        failures += bad
    print(f"requests {total}, mismatches {len(failures)}")
    for f in failures[:3]:
        print("  ", f[:300])
    sys.exit(1 if failures or total == 0 else 0)


if __name__ == "__main__":
    main()

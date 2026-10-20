"""Проверка движка на эталонных сценариях (пакетом, один процесс на документ).

Каждый шаг сценария превращается в запросы протокола; если шагу нужны
условия, сначала проверяется ответ `need`, затем результат с условиями.
Сравниваются снимок, действия, флаг восстановления и список условий.

    python3 conformance.py ENGINE [--mutate M] DOC=TRACES.jsonl [DOC=TRACES ...]
    python3 conformance.py ENGINE [--mutate M] --golden examples/golden.jsonl

Код выхода 0 — расхождений нет.
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "clients", "python"))
from protocol import ChildTimeout, ProtocolError, check_reply, default_timeout, mutant_env, run_child


def plan(trace):
    """Пары (запрос, ожидание) для одного сценария."""
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
    """Ожидание из сценария тоже обязано соответствовать протоколу."""
    if "need" in exp:
        return check_reply({"id": rid, "need": exp["need"]}, rid, op)
    e = exp["result"]
    reply = {"id": rid, "snapshot": e["snapshot"], "actions": e["actions"]}
    if e.get("recovered"):
        reply["recovered"] = True
    return check_reply(reply, rid, op)


def run(engine, doc, traces, mutate=None, timeout=None):
    """Прогнать сценарии одного документа. Возвращает (запросов, ошибки)."""
    if not traces:
        return 0, [f"{doc}: нет сценариев"]
    pairs = [p for t in traces for p in plan(t)]
    if not pairs:
        return 0, [f"{doc}: сценарии не содержат ни одного запроса"]
    cmd = [engine] + (["--mutate", mutate] if mutate else []) + [doc]
    lines = [json.dumps({**req, "id": i}, ensure_ascii=False) for i, (req, _) in enumerate(pairs)]
    try:
        out = run_child(cmd, ("\n".join(lines) + "\n").encode(), timeout or default_timeout(),
                        f"conformance {os.path.basename(doc)}", env=mutant_env() if mutate else None)
    except ChildTimeout as e:
        return len(pairs), [str(e)]
    if out.returncode != 0:
        return len(pairs), [f"движок завершился с кодом {out.returncode}: {out.stderr.decode()[:200]}"]
    raw = out.stdout.decode().split("\n")
    if raw and raw[-1] == "":
        raw.pop()
    bad = []
    if len(raw) != len(pairs):
        bad.append(f"ответов {len(raw)} из {len(pairs)}")
    for i, ((req, exp), line) in enumerate(zip(pairs, raw)):
        try:
            expected_reply(exp, i, req["op"])
        except ProtocolError as e:
            bad.append(f"сценарий: ожидание {i} нарушает протокол: {e}")
            continue
        try:
            resp = json.loads(line)
            variant = check_reply(resp, i, req["op"])
        except (ValueError, ProtocolError) as e:
            bad.append(f"ответ {i}: {e}: {line[:200]}")
            continue
        if "need" in exp:
            ok = variant == "need" and sorted(resp["need"]) == exp["need"]
        else:
            e = exp["result"]
            ok = (variant == "result" and resp["snapshot"] == e["snapshot"]
                  and resp["actions"] == e["actions"]
                  and resp.get("recovered", False) is e.get("recovered", False))
        if not ok:
            bad.append(f"{req} → {resp}, ожидалось {exp}")
    return len(pairs), bad


def load_jsonl(path):
    return [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]


def corpus(args):
    """Список (документ, сценарии) из аргументов. Пустой корпус — ошибка."""
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
        raise SystemExit("корпус пуст: не передано ни одного набора сценариев")
    for doc, traces in items:
        if not traces:
            raise SystemExit(f"корпус пуст для {doc}")
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
    print(f"запросов {total}, расхождений {len(failures)}")
    for f in failures[:3]:
        print("  ", f[:300])
    sys.exit(1 if failures or total == 0 else 0)


if __name__ == "__main__":
    main()

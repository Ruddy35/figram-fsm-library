#!/usr/bin/env python3
# Document generator of the host-perf audit harness, kept for the P-06/P-07 load/step series.
"""Параметрический генератор документов Figram для замеров масштабируемости.

    gen_doc.py --states N [--groups G] [--depth D] [--branches B] [--resume R]
               [--events shared|unique] [--timers P] > doc.json

Форма документа:
* корень: `cmd:start` → m0, `cmd:help` → help (глобальные события, всплывают
  на каждое сообщение);
* G групп; группа g — цепочка из D вложенных разделов g{g}_l0 ⊃ … ⊃ g{g}_l{D-1},
  её сообщения лежат во внутреннем разделе (глубина каждого сообщения = D);
  на каждом уровне раздела стрелка `btn:up{l}` → начальное сообщение уровня
  (проверка всплытия на глубину);
* у каждого сообщения m_i: `btn:next` и `btn:prev` с B вариантами
  (B−1 с условиями c1..c{B-1} и «иначе»), действия k1/k2 на вариантах;
* `--events unique`: дополнительно у каждого сообщения своя кнопка `btn:go{i}`
  (реалистично для больших ботов: у кнопок уникальные callback_data) —
  число различных событий растёт как N;
* `--resume R`: первые R групп — цели «вернуться туда, где был» из help
  (`btn:back{g}`), каждое сообщение внутри запоминает место;
* `--timers P`: процент сообщений с таймером t{i} и стрелкой `timer:t{i}`;
* `--global G`: ещё G глобальных команд `cmd:g{j}` на корне — всплывают на
  каждое сообщение, поэтому строк автомата ≈ N · (G + собственные события).
"""
import argparse
import json


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--states", type=int, required=True)
    ap.add_argument("--groups", type=int, default=0, help="0 → max(1, N // 10)")
    ap.add_argument("--depth", type=int, default=1)
    ap.add_argument("--branches", type=int, default=2)
    ap.add_argument("--resume", type=int, default=1)
    ap.add_argument("--events", choices=["shared", "unique"], default="shared")
    ap.add_argument("--timers", type=int, default=10)
    ap.add_argument("--global", dest="glob", type=int, default=0,
                    help="число дополнительных глобальных команд cmd:g{j} на корне")
    a = ap.parse_args()

    n = max(2, a.states)
    g_count = a.groups or max(1, n // 10)
    g_count = min(g_count, n - 1)
    depth = max(1, a.depth)
    b = max(1, a.branches)
    r_count = min(a.resume, g_count)
    conds = [f"c{j}" for j in range(1, b)]

    msgs = [f"m{i}" for i in range(n - 1)]          # n-1 рабочих + help
    groups = [[] for _ in range(g_count)]
    for i, m in enumerate(msgs):
        groups[i % g_count].append(m)

    def variants(i, step):
        vs = []
        for j, c in enumerate(conds, 1):
            vs.append({"if": c, "to": msgs[(i + step * (j + 1)) % len(msgs)], "do": ["k1"]})
        vs.append({"to": msgs[(i + step) % len(msgs)], "do": ["k2"]})
        return vs

    def message(i):
        on = {"btn:next": variants(i, 1), "btn:prev": variants(i, -1)}
        if a.events == "unique":
            on[f"btn:go{i}"] = [{"to": msgs[(i * 7 + 3) % len(msgs)]}]
        node = {"on": on}
        if a.timers and (i * 37 % 100) < a.timers:
            node["timers"] = {f"t{i}": 60000}
            on[f"timer:t{i}"] = [{"to": msgs[(i + 2) % len(msgs)]}]
        return node

    idx = {m: i for i, m in enumerate(msgs)}
    children = {}
    for g, ms in enumerate(groups):
        inner = {m: message(idx[m]) for m in ms}
        node = None
        for lvl in reversed(range(depth)):
            sid = f"g{g}_l{lvl}"
            kids = inner if node is None else {f"g{g}_l{lvl + 1}": node}
            init = ms[0] if node is None else f"g{g}_l{lvl + 1}"
            node = {"initial": init, "on": {f"btn:up{lvl}": [{"to": f"g{g}_l{lvl}"}]},
                    "children": kids}
        children[f"g{g}_l0"] = node
    help_on = {f"btn:back{g}": [{"to": f"g{g}_l0", "resume": True}] for g in range(r_count)}
    help_on["btn:home"] = [{"to": "m0"}]
    children["help"] = {"on": help_on}

    doc = {
        "figram": 1,
        "conditions": {c: f"условие {c}" for c in conds},
        "calls": {"k1": "действие 1", "k2": "действие 2"},
        "bot": {
            "initial": "g0_l0",
            "on": {"cmd:start": [{"to": "m0"}], "cmd:help": [{"to": "help"}],
                   **{f"cmd:g{j}": [{"to": msgs[(j * 13) % len(msgs)]}] for j in range(a.glob)}},
            "children": children,
        },
    }
    if not conds:
        del doc["conditions"]
    json.dump(doc, __import__("sys").stdout, ensure_ascii=False, separators=(",", ":"))


if __name__ == "__main__":
    main()

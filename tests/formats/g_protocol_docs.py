#!/usr/bin/env python3
"""(g) Consistency of the protocol documentation with the engine source.

Checks that FORMAT.md §7 states every rule the engine enforces (and nothing
contradicting HOST.md §1.3), so the doc cannot silently drift from the code:

  * every error code of `ErrCode.name` (Figram/Engine.lean) is listed in the
    §7 error table and in `ERROR_CODES` of ffi/python-example/protocol.py, and
    no other code is;
  * every field of `opKeys` (Engine.lean) is named in §7 for its operation;
  * §7 documents the id echo on parse errors, the order of replies, the request
    size limit (`--max-request`, default from `defaultMaxRequest`), invalid UTF-8,
    blank lines, `start` with `seq` and the test-only mutant mode;
  * rule 3 (need) uses the HOST.md §1.3 condition — guards are computed on data
    where the `call` actions of earlier steps are applied — and no longer says
    "по той же версии снимка" (INT-08).

    python3 g_protocol_docs.py [--format OTHER_FORMAT.md]
"""

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def read(*p):
    return open(os.path.join(ROOT, *p), encoding="utf-8").read()


fmt = open(sys.argv[2], encoding="utf-8").read() if sys.argv[1:2] == ["--format"] else read("FORMAT.md")
host, eng, proto = read("HOST.md"), read("Figram", "Engine.lean"), read("ffi", "python-example", "protocol.py")
s7 = fmt[fmt.index("## 7."):]


def part(text, a, b):
    """text from marker a to marker b ("" if a marker is missing)."""
    i, j = text.find(a), text.find(b, text.find(a) + 1)
    return text[i:j] if i >= 0 and j >= 0 else ""


fails = []


def need(cond, what):
    if not cond:
        fails.append(what)


codes = re.findall(r'\| \.\w+ => "(\w+)"', part(eng, "def ErrCode.name", "abbrev RE"))
need(len(codes) == 5, f"ErrCode.name: expected 5 codes, found {codes}")
table = [c for c in re.findall(r"^\| `(\w+)` \|", s7, re.M) if c != "code"]
need(sorted(table) == sorted(codes), f"FORMAT §7 error table {table} != ErrCode {codes}")
py = re.search(r"ERROR_CODES = frozenset\(\{([^}]*)\}\)", proto)
need(py and sorted(re.findall(r'"(\w+)"', py.group(1))) == sorted(codes), "protocol.py ERROR_CODES != ErrCode")

ops = dict(re.findall(r'\| "(\w+)" => some \[([^\]]*)\]', part(eng, "def opKeys", "def checkFields")))
need(set(ops) == {"hello", "start", "restart", "step"}, f"opKeys ops {sorted(ops)}")
fields_par = s7[s7.find("Запрос строгий"):][:600] if "Запрос строгий" in s7 else ""
for op, keys in ops.items():
    for k in re.findall(r'"(\w+)"', keys):
        need(f"`{k}`" in fields_par, f"FORMAT §7: field `{k}` of `{op}` not named in the strict-request paragraph")

limit = re.search(r"def defaultMaxRequest : Nat := (\d+)", eng)
need(limit and f"{int(limit.group(1)):,}".replace(",", " ") in s7, "FORMAT §7: default size limit not stated")
for phrase, what in [("--max-request", "size limit option"), ("FIGRAM_ALLOW_MUTANTS=1", "mutant gating"),
                     ('"mutant"', "hello.mutant"), ("строго в порядке запросов", "reply order (FIFO)"),
                     ('{"id":', "id echo"), ("UTF-8", "invalid UTF-8"), ("пропускается без ответа", "blank lines"),
                     ('"seq"?: N', "start with seq"), ("верхнюю отметку", "high-water seq for start")]:
    need(phrase in s7, f"FORMAT §7 does not document {what} ({phrase!r})")

rule3 = part(s7, "3. Если варианту стрелки", "4. Событие без подходящей")
need("по той же версии снимка" not in rule3, "FORMAT §7 rule 3 still says «по той же версии снимка» (INT-08)")
need("уже применены все действия `call`" in rule3 and "уже применены все действия `call`" in host,
     "FORMAT §7 rule 3 does not use the HOST.md §1.3 condition (INT-08)")

for f in fails:
    print("НЕТ", f)
print(f"protocol docs: codes {len(codes)}, ops {len(ops)}, failures {len(fails)}")
sys.exit(1 if fails else 0)

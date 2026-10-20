#!/usr/bin/env python3
"""Consistency check of the documentation against the code and the audit findings
(fix wave, agent fix-docs).

    python3 -I audit/docs/check_docs.py [REPO] [FIGRAM] [ENGINE]

REPO defaults to the repository of this script, FIGRAM / ENGINE to .lake/build/bin/.
Two kinds of checks:

* doc  — HOST.md, README.md, AUDIT.md, REVIEW.md, FORMAT.md (outside §7) state what the audit
         found and no longer contain the overstated claims (old text must be gone);
* real — the behaviour the documentation now describes is reproduced on the real engine,
         and numbers quoted by the documentation equal the numbers in the test sources.

Exit 0 = every check passes. FORMAT.md §7 and the README protocol/embedding sections belong to
other owners; problems there are printed as INFO and do not fail the run.
"""

import json
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else os.path.dirname(os.path.dirname(HERE))
FIGRAM = sys.argv[2] if len(sys.argv) > 2 else os.path.join(REPO, ".lake/build/bin/figram")
ENGINE = sys.argv[3] if len(sys.argv) > 3 else os.path.join(REPO, ".lake/build/bin/figram-engine")

fails = 0


def check(item, good, text):
    global fails
    print(f"{'ok  ' if good else 'FAIL'} [{item}] {text}")
    fails += 0 if good else 1


def info(item, good, text):
    if not good:
        print(f"INFO [{item}] {text}")


def read(rel):
    with open(os.path.join(REPO, rel), encoding="utf-8") as f:
        return f.read()


def section(text, num):
    m = re.search(rf"^## {num}\..*?(?=^## |\Z)", text, re.S | re.M)
    return m.group(0) if m else ""


def flat(text):
    """Text without line breaks inside paragraphs (phrases may wrap)."""
    return re.sub(r"\s+", " ", text)


HOST, README, AUDIT, REVIEW, FORMAT = (read(f) for f in ("HOST.md", "README.md", "AUDIT.md", "REVIEW.md", "FORMAT.md"))
F7 = FORMAT[FORMAT.find("## 7."):]                       # owned by fix-protocol
FMAIN = FORMAT[:FORMAT.find("## 7.")]                    # owned by fix-docs
H = {n: flat(section(HOST, n)) for n in range(1, 8)}
R_DEC = flat(README[README.find("### Решения спецификации"):README.find("## Доказанные теоремы")])
R_CHECK = flat(README[README.find("## Проверка"):README.find("## Что не покрыто")])

# ---------------------------------------------------------------- numbers from the test sources
faults_src = read("ffi/python-example/client_faults.py")


def dict_entries(src, name):
    """Entries `"name": '...'` of the dict literal `NAME = {` … closing `}` (0 if absent)."""
    m = re.search(rf"^(\s*){name} = \{{\n(.*?)^\1\}}", src, re.S | re.M)
    return len(re.findall(r"^\s+\"[^\"]+\": '", m.group(2), re.M)) if m else 0


fakes = dict_entries(faults_src, "FAKES")          # faults on a request (after hello)
start_fakes = dict_entries(faults_src, "START")    # faults before the process is ready (fix-client)
corrupt_src = read("ffi/python-example/harness_selftest.py")
corrupt = len(re.findall(r"^\s+\"[^\"]+\": f'", corrupt_src[corrupt_src.find("CORRUPT = {"):corrupt_src.find("TOOLS = {")], re.M))
go_src = read("ffi/go-example/main.go")
go_corrupt = len(re.findall(r'^\t"[a-z-]+":\s+func', go_src[go_src.find("var corruptions"):go_src.find("const maxInt")], re.M))
verify = read("verify.sh")
min_selftest = int(re.search(r"^MIN_SELFTEST_OK=(\d+)", verify, re.M).group(1))
min_mutants = int(re.search(r"^MIN_MUTANTS=(\d+)", verify, re.M).group(1))
fuzz_cases = int(re.search(r"json_fuzz\.py \$F (\d+)", verify).group(1))


def num(s):
    return int(s.replace(" ", "").replace(" ", ""))


# ---------------------------------------------------------------- P-05: idempotency keys, reset
check("P-05", "`пользователь:update_id:действие:вхождение`" in H[1],
      "HOST §1.4 recommends keys from the delivery id (`пользователь:update_id:действие:вхождение`)")
check("P-05", re.search(r"эпох", H[1]) and re.search(r"вне откатываем", H[1]),
      "HOST §1.4 offers a per-user epoch stored outside the store that can be rolled back")
check("P-05", re.search(r"`пользователь:seq:номер`[^.]*безопас\w*,? только", H[1]),
      "HOST §1.4 says when `пользователь:seq:номер` is still safe")
check("P-05", re.search(r'"op": "start", "seq": N', section(HOST, 4)) and "верхн" in H[4],
      "HOST §4: reset via `start` with `seq` = persistent high-water mark (PROTOCOL_CHANGES §4)")
check("P-05", re.search(r'"op": "restart"', section(HOST, 4)) and re.search(r"последн\w* зафиксированн", H[4]),
      "HOST §4 keeps `restart` with the latest committed snapshot / placeholder (check_host_contract.py)")
check("P-05", re.search(r"жетоном не больше `N`|жетоном ≤ `N`", H[4]),
      "HOST §4: scheduler drops the user's events with token ≤ N (robust to reordering)")

# ---------------------------------------------------------------- P-11: PH-03, PH-05, cancel_timer, HOST §7
check("PH-03", re.search(r"[Оо]бщий дефицитный ресурс", H[1]) and "до фиксации" in H[1]
      and re.search(r"зафиксированн\w*, но ещё не исполненн", H[1]),
      "HOST §1.3: scarce shared resource — lock from guard evaluation to commit, count committed-but-unexecuted reservations")
check("PH-05", re.search(r"только планировщик", H[3]) and "`token`" in H[3] and re.search(r"пользовател\w* ввод", H[3]),
      "HOST §3: `timer:*` events and `token` come only from the scheduler, never from user input")
check("CV-C7", re.search(r"жетон меньше `seq`", H[3]) and re.search(r"\(пользователь, имя, жетон\)", H[3]),
      "HOST §3: cancel_timer removes a scheduled event only if its token < seq of the cancelling reply (or key by user,name,token)")
check("CV-C7", "отмена — оптимизация, а не условие корректности" not in H[3],
      "HOST §3: the unconditional «отмена — оптимизация» claim is gone")
m = re.search(r"(\d+) вид\w* сбоев", H[7])
m2 = re.search(r"и (\d+) — до готовности", H[7])
check("D-06/PC-17", m and int(m.group(1)) == fakes and "шест" not in H[7]
      and (start_fakes == 0 or (m2 and int(m2.group(1)) == start_fakes)),
      f"HOST §7: number of client fault kinds = {fakes} on a request (client_faults.py FAKES)"
      f" and {start_fakes} before ready (START)")

# ---------------------------------------------------------------- P-15 / ET-05 / ET-06
check("ET-06", re.search(r"без перехода", H[3]) and re.search(r"остаётся в `timers`", H[3])
      and re.search(r"пока `seq`[^.]* больше жетона", H[1] + H[3]),
      "HOST §1.2/§3: accepted fire without a transition keeps the entry; dedup `timer:<имя>:<жетон>` until seq > token")
check("ET-05", re.search(r"все таймеры", R_DEC) and re.search(r"петл\w* таймера", R_DEC + H[3]),
      "README decision 3 / HOST §3: a self-loop (also a timer self-loop) restarts ALL timers of the message")
check("ET-05", re.search(r"не сработает никогда", H[3]) and re.search(r"[Рр]екомендац|[Сс]оветуем|[Лл]учше", H[3]),
      "HOST §3: design recommendation for periodic tick + deadline")

# ---------------------------------------------------------------- P-24: CC-06, CC-07, CC-08, FC-05, FC-02
k3 = flat(FMAIN[FMAIN.find("**К3. Минимальность.**"):FMAIN.find("**К4.")])
check("CC-06/D-07", "ведут себя одинаково, но записываются по-разному" not in k3 and "`timers`" in k3,
      "FORMAT К3в: key order is significant for `timers` (old «ведут себя одинаково» claim gone)")
check("CC-07/D-08", re.search(r"256", flat(section(FORMAT, 4))) and re.search(r"126", flat(section(FORMAT, 4))),
      "FORMAT §4: JSON depth 256 ⇒ ≈126 nested sections")
check("CC-08 A1", re.search(r"вне этого раздела", R_DEC), "README decision 5: remembered message moved out of the section")
check("CC-08 A4", re.search(r"8\. .*решени\w* 4", R_DEC), "README decision 8: entry of the initial message overwrites places (decision 4)")
check("CC-08 A6", re.search(r"запись таймера остаётся", R_DEC), "README decision 6/7: fired timer without arrow keeps its entry")
check("CC-08", re.search(r"до проверки жетона", R_DEC), "README decision 8: recovery happens before the token check")
num_txt = flat(FMAIN[FMAIN.find("**Числа.**"):FMAIN.find("### Что остаётся")])
check("FC-05", "double" in num_txt and "jsonschema" in num_txt, "FORMAT «Числа»: schema validators on doubles may accept non-integers")
check("FC-02", re.search(r"-0\.0.{0,40}0\.0", num_txt), "FORMAT «Числа»: −0 is written as 0")

# ---------------------------------------------------------------- D-01 / D-02 numbers
check("D-01", all(re.search(r"VERIFY\.log.{0,400}?будет заменён журналом CI", flat(t)) for t in (AUDIT, REVIEW)),
      "AUDIT/REVIEW: VERIFY.log will be replaced by the CI log at release")
for name, txt in (("README", README), ("AUDIT", AUDIT), ("REVIEW", REVIEW)):
    t = flat(txt)
    bad = [s for s in ("~90 000", "~11 мкс", "120 000", "~23 000", "1000 горутин", "Замеры на машине с одним ядром",
                       "в среде аудита одно ядро", "(~10 с)", "~160 с", "около 160 секунд", "1000 сообщений — 1 с")
           if s in t]
    check("D-02", not bad, f"{name}: no unsupported numbers left {bad if bad else ''}")
check("D-02", "## Производительность" in README and "до оптимизации" in README,
      "README: performance section with measured numbers marked «до оптимизации» and a slot for the post-fix numbers")
m = re.search(r"(\d+) вид\w* сбоев движка", R_CHECK)
m2 = re.search(r"сбоев движка[^|]*?и (\d+) — до готовности", R_CHECK)
check("D-02", m and int(m.group(1)) == fakes and (start_fakes == 0 or (m2 and int(m2.group(1)) == start_fakes)),
      f"README: client fault kinds = {fakes} on a request and {start_fakes} before ready")
m = re.search(r"(\d+) вид\w* порчи ответов движка, неполном.*?\((\d+) проверок\)", R_CHECK)
check("D-02", m and int(m.group(1)) == corrupt and int(m.group(2)) == min_selftest,
      f"README: harness corruption kinds = {corrupt}, self-test checks = {min_selftest}")
m = re.search(r"(\d[\d \u00a0]*) случаев в `verify.sh`", R_CHECK)
check("D-02", m and num(m.group(1)) == fuzz_cases, f"README: JSON fuzz cases = {fuzz_cases}")
m = re.search(r"обнаружено (\d+) из (\d+) мутаций", R_CHECK)
check("D-02", m and int(m.group(1)) == int(m.group(2)) == min_mutants, f"README: mutations = {min_mutants}")
a2 = flat(section(AUDIT, 2))
m = re.search(r"(\d+) вид\w* порчи ответов", a2[a2.find("Go-обёртка"):])
check("D-02", m and int(m.group(1)) == go_corrupt, f"AUDIT: Go corruption kinds = {go_corrupt}")
m = re.search(r"(\d+) вид\w* сбоев движка", a2)
m2 = re.search(r"сбоев движка[^|]*?и (\d+) — до готовности", a2)
check("D-02", m and int(m.group(1)) == fakes and (start_fakes == 0 or (m2 and int(m2.group(1)) == start_fakes)),
      f"AUDIT: client fault kinds = {fakes} on a request and {start_fakes} before ready")
check("D-02", "4 вида порчи ответов ловятся" not in a2, "AUDIT §2: stale «4 вида порчи» claim gone")
check("D-02", re.search(r"6 видов сбоев[^|]*на момент", flat(REVIEW)) or "6 видов сбоев" not in REVIEW,
      "REVIEW N01: «6 видов» marked as historical")

# ---------------------------------------------------------------- D-03/D-04/D-05/D-06 leftovers
check("D-05", "lean_mark_persistent" not in flat(REVIEW) or re.search(r"lean_mark_persistent[^.]*устарел", flat(REVIEW)),
      "REVIEW: `lean_mark_persistent` marked as outdated (now `lean_mark_mt` + `figram_unload`)")
check("D-05/T-12", re.search(r"ASan и UBSan[^|]*\|[^|]*кучу Lean", R_CHECK),
      "README check table: ASan/UBSan row says the sanitizers do not see the Lean heap")
a1 = flat(section(AUDIT, 1))
check("D-03", re.search(r"`Engine.reply_readable` \|[^|]*модели JSON `J`", a1) and re.search(r"`Engine.handle_step` \|[^|]*`handleObj`", a1),
      "AUDIT §1: reply_readable / handle_step are statements about the J model / handleObj, not the text protocol")
check("D-04", re.search(r"Решение 7[^.]*`Engine.decEvent`", a1),
      "AUDIT §1: decision 7 (timer token) is a property of Engine.decEvent, not of the core model")
check("P-18", "рантайм Lean заменяет символом U+FFFD, поэтому инварианты строк не нарушаются" not in flat(AUDIT),
      "AUDIT §4: the «U+FFFD is safe» claim is replaced (invalid UTF-8 → parse_error, PROTOCOL_CHANGES §6)")
info("D-06/INT-08", "по той же версии снимка" not in flat(F7),
     "FORMAT §7 rule 3 still says «по той же версии снимка» (owner fix-protocol; should point to HOST §1.3 barrier)")
info("FC-05", "ровно то, что разрешает `snapshot.schema.json`" not in flat(F7) or "double" in flat(F7),
     "FORMAT §7 «движок принимает ровно то, что разрешает snapshot.schema.json» has no double caveat (owner fix-protocol)")

# ---------------------------------------------------------------- T-12 / T-13
check("T-12", "mimalloc" in AUDIT and re.search(r"функциональн\w* стресс", flat(AUDIT)),
      "AUDIT: sanitizers do not see the Lean heap (mimalloc); functional stress with answer comparison is the oracle")
check("T-13", re.search(r"эквивалентн", flat(AUDIT)) and re.search(r"эквивалентн", R_CHECK + flat(README[README.find("Мутационное тестирование портит"):])),
      "AUDIT/README: `--mutants` may produce equivalent mutants on documents with unreachable branches (ET-13)")


# ---------------------------------------------------------------- real: behaviour the docs describe
def engine(doc, reqs):
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(doc, f)
    try:
        p = subprocess.run([ENGINE, f.name], input="".join(json.dumps(r) + "\n" for r in reqs),
                           capture_output=True, text=True, timeout=60)
        return [json.loads(l) for l in p.stdout.splitlines() if l.strip()]
    finally:
        os.unlink(f.name)


def figram_check(doc):
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(doc, f)
    try:
        return subprocess.run([FIGRAM, "check", f.name], capture_output=True, timeout=60).returncode
    finally:
        os.unlink(f.name)


def bot(children, **kw):
    return {"figram": 1, **kw, "bot": {"initial": next(iter(children)), "children": children}}


if not (os.access(ENGINE, os.X_OK) and os.access(FIGRAM, os.X_OK)):
    check("real", False, f"binaries not found: {FIGRAM}, {ENGINE} (lake build)")
else:
    def acts(r):
        return [next(iter(a.items())) for a in r.get("actions", [])]

    # CC-06: order of `timers` keys = order of start_timer / cancel_timer
    def timers_doc(order):
        return bot({"home": {"on": {"btn:go": [{"to": "m"}]}},
                    "m": {"timers": {k: 1000 for k in order}, "on": {"btn:back": [{"to": "home"}]}}})
    ab = engine(timers_doc("ab"), [{"id": 1, "op": "step", "snapshot": {"at": "home"}, "event": "btn:go"}])
    ba = engine(timers_doc("ba"), [{"id": 1, "op": "step", "snapshot": {"at": "home"}, "event": "btn:go"}])
    check("real CC-06", acts(ab[0])[:2] == [("start_timer", "a"), ("start_timer", "b")]
          and acts(ba[0])[:2] == [("start_timer", "b"), ("start_timer", "a")],
          "permuting `timers` keys permutes start_timer actions (as FORMAT К3в now says)")

    # CC-07: nesting limit
    def nested(k, arrow):
        leaf = {"on": {"btn:x": [{"to": "m", "do": ["c"]}]}} if arrow else {}
        node = {"initial": "m", "children": {"m": leaf}}
        for i in range(k):
            node = {"initial": f"s{i}", "children": {f"s{i}": node}}
        d = {"figram": 1, "bot": node}
        if arrow:
            d["calls"] = {"c": "C"}
        return d
    lim = (figram_check(nested(126, False)), figram_check(nested(127, False)),
           figram_check(nested(124, True)), figram_check(nested(125, True)))
    check("real CC-07", lim == (0, 1, 0, 1),
          f"126 nested sections accepted, 127 rejected; with an arrow+do in the deepest message 124/125 (got {lim})")

    # CC-08 A1: remembered message moved out of the section → initial message of the section
    a1 = engine({"figram": 1, "bot": {"initial": "home", "on": {"btn:back": [{"to": "S", "resume": True}]},
                                       "children": {"home": {}, "b": {}, "S": {"initial": "a", "children": {"a": {}, "c": {}}}}}},
                [{"id": 1, "op": "step", "snapshot": {"at": "home", "resume": {"S": "b"}}, "event": "btn:back"}])
    check("real CC-08 A1", a1[0]["snapshot"]["at"] == "a", "resume to a place moved out of the section → initial message")
    # CC-08 A4 + recovery before the token check
    a4doc = {"figram": 1, "bot": {"initial": "S", "children": {
        "T": {"initial": "t1", "children": {"t1": {}, "t2": {}}},
        "S": {"initial": "a", "children": {"a": {}, "c": {}}}},
        "on": {"btn:back": [{"to": "S", "resume": True}], "btn:back2": [{"to": "T", "resume": True}]}}}
    a4 = engine(a4doc, [{"id": 1, "op": "step", "snapshot": {"at": "gone", "resume": {"S": "c", "T": "t2"}}, "event": "btn:x"},
                        {"id": 2, "op": "step", "snapshot": {"at": "S", "timers": {"q": 3}, "seq": 3}, "event": "timer:q", "token": 3}])
    check("real CC-08 A4", a4[0].get("recovered") and a4[0]["snapshot"].get("resume") == {"S": "a", "T": "t2"},
          "recovery keeps places except those overwritten by entering the initial message")
    check("real CC-08", a4[1].get("recovered") and ("cancel_timer", "q") in acts(a4[1]),
          "snapshot whose `at` is a section is recovered before the timer token is looked at")

    # ET-06 / A6: accepted fire without a transition keeps the entry; a second delivery is accepted again
    et6doc = bot({"m": {"timers": {"t": 1000, "u": 5000}, "on": {"timer:t": [{"if": "g", "to": "done"}], "btn:go": [{"to": "done"}]}},
                  "done": {}}, conditions={"g": "G"})
    s = {"at": "m", "timers": {"t": 1, "u": 1}, "seq": 1}
    r = engine(et6doc, [{"id": 1, "op": "step", "snapshot": s, "event": "timer:t", "token": 1, "guards": {"g": False}},
                        {"id": 2, "op": "step", "snapshot": s, "event": "timer:t", "token": 1, "guards": {"g": True}},
                        {"id": 3, "op": "step", "snapshot": s, "event": "timer:u", "token": 1},
                        {"id": 4, "op": "step", "snapshot": s, "event": "btn:go"}])
    check("real ET-06", r[0]["snapshot"] == s and r[0]["actions"] == [] and r[1]["snapshot"]["at"] == "done"
          and r[2]["snapshot"] == s and ("cancel_timer", "u") in acts(r[3]),
          "fire with false guard / without arrow: snapshot unchanged, entry kept, redelivery accepted, exit cancels the fired timer")

    # ET-05: a timer self-loop restarts every timer of the message
    et5 = engine(bot({"m": {"timers": {"tick": 1000, "timeout": 10000},
                            "on": {"timer:tick": [{"to": "m"}], "timer:timeout": [{"to": "done"}]}}, "done": {}}),
                 [{"id": 1, "op": "step", "snapshot": {"at": "m", "timers": {"tick": 1, "timeout": 1}, "seq": 1},
                   "event": "timer:tick", "token": 1}])
    check("real ET-05", ("cancel_timer", "timeout") in acts(et5[0]) and ("start_timer", "timeout") in acts(et5[0]),
          "tick self-loop cancels and restarts the neighbouring timeout")

    # FC-05: schema on doubles accepts what the exact decoder rejects
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        f.write('{"figram":1,"bot":{"initial":"home","children":{"home":{}}}}')
    try:
        p = subprocess.run([ENGINE, f.name], input='{"id":1,"op":"step","snapshot":{"at":"home","seq":1e-400},"event":"btn:x"}\n',
                           capture_output=True, text=True, timeout=60)
        eng_rejects = "error" in json.loads(p.stdout.splitlines()[0])
    finally:
        os.unlink(f.name)
    try:
        import jsonschema
        schema = json.loads(read("snapshot.schema.json"))
        schema_ok = jsonschema.Draft202012Validator(schema).is_valid(json.loads('{"at":"home","seq":1e-400}'))
        check("real FC-05", eng_rejects and schema_ok, "seq 1e-400: snapshot.schema.json (jsonschema, doubles) accepts, engine rejects")
    except ImportError:
        check("real FC-05", eng_rejects, "seq 1e-400: engine rejects (jsonschema not installed, schema side skipped)")

print(f"check_docs: {fails} failure(s)")
sys.exit(1 if fails else 0)

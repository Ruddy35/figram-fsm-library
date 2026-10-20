# integration — независимая проверка исправлений и сборка ветки audit/integration

Агент: integration (волна 2). Baseline: `audit/baseline` = d69bbaf. Ветка `audit/integration`, рабочее дерево `/home/user/figram-audit-worktrees/integration`.

**Итоговый HEAD: `8be73ac2a9f76ba7dae0e3e6ec503b312d79a18f`** (baseline + 40 коммитов; раунд 1 закончился на 28a3439, см. раздел 7).

Вспомогательные ветки (только доказательства, не для слияния): `audit/integration-{fc,cc,docs,py,pf,ffi}`.

Тулчейн: Lean 4.35.0 (eb6cafb7), собран из исходников gcc/libstdc++ (stage1, `source /home/user/figram-audit/env.sh`). Для сравнения «до» использовались `fresh/figram`, `fresh/figram-engine` и `fresh/ffi-out`.

Все пути LOGS/ ниже — относительно `/home/user/figram-audit/agents/integration/`. Сводка — `LOGS/00-SUMMARY.txt`, коммиты — `LOGS/80-integration-commits.txt`.

## 1. Scope & contract

Для каждого предложенного исправления проверено на настоящем тулчейне:
- регрессионный тест красный до и зелёный после;
- сборка без предупреждений;
- `#print axioms` по ВСЕМ теоремам не изменился;
- адаптированный verify.sh зелёный;
- golden.jsonl не изменился.

После этого собрана ветка integration: baseline плюс только проверенное, тесты раньше исправлений, по одному коммиту на исправление, авторство сохранено. На ней прогнаны все использованные наборы.

`bin/` не пересобирался.

Свой инструмент `scripts/all_axioms.lean` перебирает все `thmInfo` модулей `Figram*`, включая автолеммы, и считает их аксиомы через `collectAxioms`. В нём два отрицательных контроля — `sorryAx` и собственная аксиома; оба увидены красными (LOGS/02).

## 2. Таблица исправлений (раунд 1)

| исправление | ветка/коммит → integration | регрессионный тест | FAIL до (лог) | PASS после (лог) | доказательства | вердикт |
|---|---|---|---|---|---|---|
| FC-01 (печать числа с показателем < −1000) + FC-06 (экранирование диагностики CLI) | formats-correctness 4a38b91 (тесты 897bfc8, 177355a) → 71785b8 (тесты 5e32b98, 224229d) | `b_rfc_table --strict fc01`, `a_roundtrip --strict`, `fc01_lean_check.lean`, `json_fuzz 30000` (повторное чтение самим Figram), `e_cli_names --strict` | все rc=1: fc-before-*.log (156 расхождений; EDGE 3 из 3; 4 сбоя Lean; 26 из 9786 записей не читаются; 8 из 9 строк) | все rc=0: fc-after-*.log, на integration fc-int-*.log | полная сборка 0 предупреждений; 600 теорем, аксиомы побайтно = baseline (LOGS/12) | VERIFIED |
| FC-01, альтернатива (d116a64, proofs) | proofs d116a64 → 0ce9a5f, оставлена только правка `audit/proofs/Counterexamples.lean` | `TextRoundTrip.lean`, `fmt_exponent.sh` + весь набор formats | LOGS/31-pf-TextRoundTrip-OLD (5 из 15 false), 30-pf-fmt_exponent-OLD (exit 0 = ошибка воспроизведена) | 43-pf-d116a64-* (15 из 15, exit 1); fc-pf-d116a64-*: FC-01 зелёный, `e-cli-strict` красный — FC-06 этот коммит не чинит | — | VERIFIED, но вытеснено 4a38b91 (обоснование ниже) |
| CC-01 = ET-02: таймер старой версии документа не отменяется и срабатывает на чужом сообщении | патч core-compiler `proposed_fix_UNVERIFIED.patch` + мои доработки → d3782f7; тесты: bc945f0, dd5d4e3, 22f0203 (U4h/U5h), 906d3d0 (`CC01=fixed`) | `directed.py --group findings` (H2 ×2); ptsuite U4h/U5h; `diff_random` с designer-оракулом (обновления, возмущения, перебор); `repro.py` H2 и H2_min | старый движок: 25-cc-directed-findings-OLD (FAIL), 26-cc-repro-H2-OLD (exit 1, вызов k1 на D), 32-et-ptsuite-upgrade-OLD (U4h/U5h FAIL), cc-designer-full-OLD-scratch и cc-designer-upgrades-OLD-scratch (exit 1) | новый: 25-*-NEW (H2 PASS), 26-*-NEW (exit 0), 32-*-NEW (PASS), designer-full: 0 расхождений на 600 документах и 496 975 запросах (353 обновления, 638 возмущений extra_timer); integration: 65-int-cc-suite (25 из 26 ok, см. INT-05), 61-int-et-ptsuite-full (56 сценариев, 0 FAIL), 62-int-et-walk-* (0 нарушений на ≈46 тыс. проверок), 66-int-* | патч как есть не собирался (LOGS/20: 3 ошибки `rfl`/типов в `compile_correct`, 1 в `step_snapOK`). Восстановлено без изменения формулировок (LOGS/21, 23); 601 теорема, аксиомы = baseline плюс `staleCancels.eq_1 : []` (LOGS/24, 52); strict gate PASS; FigramAudit.Glue собирается, leanchecker ok (LOGS/54) | VERIFIED-WITH-NOTES |
| python-client: PC-01, 02, 03, 10, 12 | python-client 51c31ce (тесты 1df3ef8, 2550540, bbf7ac7, d8f658b) → 31f9eab (тесты 4121041, fff6018, ae95128, 52d53af) | `client_robustness.py` R5–R9; `client_faults.py`, `client_contract.py` | py-before-client_robustness rc=1 на fresh-движке: R5 — 200 UnicodeEncodeError и 200 ожидающих; R6 — пул воскресает; R7 — FileNotFoundError; R8 — соседи получают 10–15 ошибок; R9 — 99 killpg по свободному pid | py-after-* все rc=0 (fresh-движок); py-int-* rc=0 (движок integration); client_contract 20 из 20, client_robustness 5 из 5 стабильно | — | VERIFIED |
| ET-01, ET-03, ET-11 — документация: start/restart, последний зафиксированный снимок, таймеры между версиями | engine-timers 998a539 (тесты c9b7a1a, c6eecd0, 8edc9a4) → 7730517 | `check_host_contract.py` | 33-et-host-contract-BASELINE-docs rc=1 (4 сбоя, 10 из 10 ключей совпадают) | 33-…-MERGED-docs rc=0, 60-int-et-host-contract rc=0 (0 совпадений) | — | VERIFIED |
| HP-01…HP-09 — документация HOST §1.2/1.3/1.5/2/5/7 + host_sim.py (HP-07) | host-perf c8bb903, 1dfe574 → 8fdcb4b (без hunk'а `host_sim_mutants.py`), 9f9ea35; плюс моё исправление теста 253df6b | `host_sim.py` с барьером (должен быть зелёным) и `--no-barrier` (должен быть красным) | старый §1.3 = `--no-barrier`: 10 из 10 красных, у 0 из 30 пользователей итог как ожидалось (34, 35, 63) | с барьером 30 из 30 exit 0 (64-int-host_sim-retryctl-barrier-x30); `--no-barrier` 10 из 10 exit 1 (64-…-no-barrier-x10) | — | VERIFIED-WITH-NOTES (INT-03) |
| F-PROOFS-01 — строгий шлюз доказательств в verify.sh | proofs 90201dd (+ e0091ba, 44f8b39, b5bc7e9) → d381d6e (+ 9d25198, d1defb4, b733f4f) | `axiom_gate_canary.sh` | 71-int-canary-baseline-verify: старый шаг verify.sh обманут (axiom, native_decide, addDeclCore) | 71-int-canary-integration-verify: новый шаг красный, все ожидания выполнены; чистое дерево зелёное (53, 70) | AxiomGate, Statements (+ отрицательный контроль), Counterexamples (+ отрицательный контроль), leanchecker Figram и FigramAudit (54) | VERIFIED |
| F-PROOFS-03 — формулировки теорем в README/FORMAT | proofs d9934d5 → 793d688 | ревью; согласовано с моими правками решений 2 и 7 | — | — | — | VERIFIED (DOC) |
| FFI-01, 05, 06 — figram_ffi.c | ffi-go 4998244 (тесты 96ca9aa) → 2385d49 (тесты 4547a2f) | `ffi/audit/regress.sh`, `init_race.sh` 300, `TestAuditInitRace` | ffi-before-*: regress rc=1 (FFI-01, 05, 06 — SIGSEGV), init_race 12 из 300 SIGSEGV, Go 25 из 50 | ffi-after-* и ffi-int-*: regress rc=0, 0 из 300, Go 0 из 50 | — | VERIFIED |
| FFI-08, 11, 12 — Go-обёртка | ffi-go a027905 → 17b0609; моё исправление теста 8134324 | TestAuditHelloAlwaysError, RequestBadOp, LoadNUL; `go_verify_block.sh`; Go-блок verify.sh | ffi-before-go_test_race FAIL | ffi-after/int-go_test_race PASS; Go-блок verify.sh зелёный (70) | — | VERIFIED (флейк INT-02 существовал и на baseline) |

### Выбор между двумя исправлениями FC-01: 4a38b91 или d116a64

Условие в `render` в обоих коммитах — один и тот же предикат: `e > 0 || (e < -30 && -maxExponent ≤ e)`; запись `e ≥ -maxExponent` в d116a64 — то же отношение. Оба печатают такие числа позиционно («точной десятичной дробью»).

Проверено (LOGS/44, `REPRODUCTIONS/fc01_fix_equivalence/compare.sh`):
- вывод `figram json` двух сборок на 20 156 входах побайтно одинаков;
- от baseline отличаются 124 строки;
- все 20 156 выводов читаются обратно;
- самая длинная строка — 1508 байт;
- golden.jsonl не меняется ни с одним из них;
- json_fuzz зелёный с обоими.

Выбран 4a38b91, потому что:
- в нём же FC-06 (`sanitize`), который нужен в любом случае;
- он проверен более широким набором: formats плюс json_fuzz, перечитывающий собственный вывод;
- его текст FORMAT.md дополнительно документирует FC-02 (−0 → 0).

От d116a64 оставлена правка `Counterexamples.lean`: старый `#guard`, утверждавший ошибку, иначе ломается после исправления.

### CC-01: что изменено относительно предложенного патча

1. Восстановлены доказательства.
   - `compile_correct`: равенство `exitOf = exitActs` переписывается до `split` по условию приёма события.
   - `step_snapOK`: добавлен четвёртый случай — действия `cancelTimer` из `staleCancels`.
   - Формулировки всех 79 теорем исходников не менялись: `Statements.lean` и `ci/THEOREMS.expected` совпадают, сдвинулись только номера строк — 7e41da3.
2. Правило `ownsTimer` добавлено в `Engine.missing` и `Trace.guardNames/firedIndex`. Иначе `need` запрашивался бы для события, которое шаг затем проигнорирует. Отчёт core-compiler сам называл эту правку нужной, но в патч она не вошла. Designer-оракул (для проигнорированного события `need` не задаётся) согласен с движком: 0 расхождений на 496 975 запросах. Вариант без этой правки не собирался — сравнения «что было бы без неё» нет.
3. Документация, описывавшая поведение без исправления, переписана: README (решения 2 и 7), FORMAT §7 (правило 2), HOST §6 (пункт про таймеры). Тест `check_host_contract` по-прежнему зелёный.
4. Тесты, ожидавшие старую семантику, переведены на исправленную (906d3d0, `CC01=fixed` по умолчанию; `CC01=baseline` возвращает исходные ожидания):
   - на старом движке с ними красные: diff-random, designer-upgrades, mutants, lint-gaps (LOGS/67, cc-designer-*-OLD);
   - на новом — зелёные все шаги, кроме mutants (INT-05).

В пределах одной версии документа поведение не изменилось:
- golden, 18 103 запроса conformance, 100 случайных документов, мутации 107 из 107 (verify.sh, LOGS/70, 93);
- designer-same-version: 0 расхождений.

Остаточное:
- H3 / CC-04 (лишние условия в `need`) не исправлялся: directed-findings остаётся exit 1.
- Запись устаревшего таймера живёт в снимке, пока пользователь стоит на сообщении. Её срабатывание игнорируется, а при первом переходе выдаётся `cancel_timer`.

### Слияние HOST.md

998a539 и c8bb903 слились без текстовых конфликтов. Сохранены правила обоих:
- §1.2 — таблица отсева повторов;
- §1.3 — барьер;
- §1.4 — ключ уникален, пока не повторяется `seq`;
- §1.5 — накопительный ack;
- §2 — FIFO по пользователю, отказы, срок таймера, `send` «хотя бы один раз»;
- §4 — `restart` с последним зафиксированным снимком и заглушкой `{"at":"-","seq":N}`;
- §5 — детерминированные ошибки, таймаут загрузки;
- §6, §7.

Пункт §6 про таймеры заменён описанием исправленного движка (в коммите CC-01). Ссылка на `host_sim_mutants.py` в c8bb903 убрана: этот файл из аудиторского набора в integration не вошёл.

Проверка смысла:
- host_sim по-прежнему вызывает `start` только для нового пользователя без снимка — это согласуется с новым §4;
- `check_host_contract.py` зелёный, host_sim зелёный с барьером и красный без барьера.

## 3. Таблица гарантий

| guarantee | test | oracle | actually run? | coverage | gap |
|---|---|---|---|---|---|
| Сборка без предупреждений | `rm -rf .lake/build; lake build` | 0 строк warning | да, LOGS/51 (42 задания, все пересобраны); раунд 2 — LOGS/90 | все модули | — |
| Только стандартные аксиомы, все теоремы | `scripts/all_axioms.lean` + `strict_gate.sh` + `ci/theorems.lean` | `collectAxioms` ⊆ {propext, Quot.sound, Classical.choice}; контроли sorryAx и axiom краснеют | да, LOGS/02, 12, 24, 52, 53, 96, 93 | 601 теорема окружения, 79 теорем исходников, 17 закреплённых формулировок | одно ядро Lean; lean4lean не запускался |
| Шлюз ловит подлог | `axiom_gate_canary.sh`; `verify-faults full` custom-axiom | ожидаемые red/green | да, LOGS/71; faults-full-int | axiom, native_decide, addDeclCore | — |
| Исправления FC не меняют модель документа | golden, a_roundtrip 1265, c_schema 103, d_engine 65 | сохранённые эталоны | да, fc-int-* | 253 документа × 5 | — |
| CC-01: на устаревшем таймере нет перехода, при уходе — отмена | directed H2, ptsuite U4h/U5h, repro H2 / H2_min | ожидания по HOST §6 и решению 7 | да: FAIL до, PASS после | 2 + 2 сценария + 2 repro | — |
| CC-01: исправленная семантика = независимый оракул | `diff_random` с designer-оракулом, обновления + возмущения + перебор | oracle.py core-compiler (`timer_accept=owned`, `exit_cancels=snapshot`) | да: NEW — 0 расхождений на 496 975 запросах; OLD — красный | 600 документов, 353 обновления, 4 438 возмущений | общая ошибка прочтения прозы возможна |
| В пределах одной версии поведение не меняется | verify.sh: golden, conformance, random_docs, mutation, protocol_edges; designer-same-version | движок против эталона | да, LOGS/70, 93, 65 | 18 103 + 97 282 запроса, 11 граничных случаев | — |
| Клиент Python | robustness / faults / contract | см. тесты агента | да: before / after / int, повторы ×20 и ×5 | R1–R9 | PC-05, 06, 07, 15, 16, 18 не исправлены |
| Контракт хоста | check_host_contract, host_sim ± барьер | ключи не совпадают; итог по пользователям | да, 60, 63, 64 | pizza | host_model.py (HP-02…09) не перепрогонялся |
| FFI / Go | regress.sh, init_race 300, `go test -race` аудита, Go-блок verify.sh | коды возврата, отсутствие гонок | да, ffi-before/after/int-*, 93 | 300 процессов, 50 запусков Go | TSan/ASan-варианты contract/stress и soak/scaling/signals не запускались |
| Шлюз verify.sh/verify-prebuilt.sh красный на неисправностях | `verify-faults.sh` prebuilt (8 случаев) и full (5) | коды 0/1/3 | да, faults-prebuilt-releasesim, faults-full-int | 7 + 4 неисправности | full — только 4 из 12 неисправностей |

## 4. Findings

Общие поля: baseline d69bbaf; ветка audit/integration; бинарники — собственная сборка stage1.

### INT-01 — bin/ не соответствует исправленным исходникам; verify-prebuilt.sh красный до пересборки
- Модуль: bin/, verify.sh, verify-prebuilt.sh. Класс: TEST/BUILD (процесс выпуска).
- Путь: любой, кто запускает verify-prebuilt.sh или поставляет `bin/` из ветки integration.
- Факты:
  - verify-prebuilt.sh на integration падает на шаге json_fuzz: старый `bin/figram` не читает обратно 26 из 9786 записей (раунд 1, LOGS/72) и 12 из 8962 после генератора NaN/Infinity (раунд 2, faults-prebuilt-realtree) — в нём FC-01;
  - усиленный verify.sh без отступления падает на шаге хешей (LOGS/92), с `FIGRAM_SHA256SUMS` даёт код 3.
- Последствие: выпущенные бинарники содержат FC-01, FC-06 и CC-01.
- Риск 3. P3. CONFIRMED.
- Исправление (процесс выпуска):
  1. пересобрать официальным Lean 4.35.0 через elan и CI, как в run 39330382243;
  2. удалить отладочную информацию (strip), заменить `bin/figram*`, `bin/SHA256SUMS`, `VERIFY.log` — отдельным коммитом;
  3. не переносить TEST-ONLY коммиты (9778dac, 8be73ac);
  4. затем усиленный verify.sh должен дать 0 («ok: хеши совпадают побайтно»), verify-prebuilt.sh — тоже 0.
- Имитация выпуска (копия дерева, `bin/` = локальная сборка HEAD): verify-faults prebuilt даёт `none` 0 и все 7 неисправностей 1 (faults-prebuilt-releasesim).
- `libfigram.a` поставляется только из исходников, поэтому исправления FFI не зависят от `bin/`.

### INT-02 — нестабильная проверка памяти Go `-reload` (порог 2048 КБ)
- Модуль: `ffi/go-example/main.go`, шаг Go в verify.sh. Класс: TEST. Риск 2. P3. CONFIRMED.
- Факты: на baseline порог превышен в 1 из 6 прогонов (2520 КБ); в integration — в 2 из 4 (2132 и 2216 КБ). Контроль без Close — около 50 МБ.
- То же, что H16 (harness-ci). Исправлено в 4b7b3ee (= 1cd4a74): порог 4096 КБ, в 12 раз ниже контроля. В раунде 2 Go-шаг зелёный (LOGS/93).

### INT-03 — вероятностный отрицательный контроль host_sim даёт ложный красный
- Модуль: `ffi/python-example/host_sim.py`. Класс: TEST. Риск 2. P3. CONFIRMED.
- Контроль остался зелёным, и host_sim вернул exit 1: на baseline в 1 из 31 прогона, после слияния c8bb903/1dfe574 — в 3 из 31 (LOGS/34, 35, 63).
- Исправление 253df6b: до трёх попыток контроля, достаточно одной красной; основной сценарий не повторяется.
- Повторная проверка: 30 из 30 зелёных; `--no-barrier` 10 из 10 красных (LOGS/64). Ожидания шлюза в verify.sh и verify-prebuilt.sh подогнаны под формат вывода (56d67c6).

### INT-04 — гонка данных в тесте TestAuditThreadCount (ffi-go)
- Модуль: `ffi/go-example/audit_test.go`. Класс: TEST. Риск 2. P3. CONFIRMED.
- Горутина-сэмплер пишет `maxT`, а тест читает его без связи happens-before. Под `-race` тест упал (ffi-after-go_test_race: DATA RACE, audit_test.go:481).
- Исправление 8134324: тест ждёт завершения сэмплера. Повторная проверка: 3 из 3 (ffi-after-threadcount-race-x3) плюс прогоны на integration.

### INT-05 — классификатор mutants_red.py не учитывает исправление CC-01
- Модуль: `audit/core-compiler/mutants_red.py`. Класс: TEST. Риск 1. P3. CONFIRMED.
- На исправленном движке из 2517 мутантов 490 выживших классифицированы как неэквивалентные (cc-suite-INT):
  - 488 — мутации стрелок `timer:X` на сообщениях, которые не владеют X. После исправления такие стрелки недостижимы через протокол (`ownsTimer = false`; `decEvent` превращает `timer:` в `.timer`), то есть мутанты эквивалентны;
  - 2 не классифицированы: `drop-exit` на документе 700023 и `reverse-entry:4` на документе 700026.
- Отдельно: `staleCancels` частично маскирует мутант `drop-exit`. Отмены всё равно выдаются, мутант виден только по порядку отмен и по приёму таймера. verify.sh по-прежнему находит 107 из 107 мутаций.
- Шаг mutants в run_all — WRONG; остальные 25 из 26 шагов ok.
- Предложение: в режиме `CC01=fixed` считать стрелки таймеров на сообщении-не-владельце мёртвыми; разобрать два оставшихся мутанта. Не исправлено.

### INT-06 — strict_gate.sh перезаписывает отслеживаемые файлы
- Модуль: `audit/proofs/strict_gate.sh` (gen_axioms.py → AllAxioms.lean, theorems.txt). Класс: TEST. Риск 1. P3. CONFIRMED.
- Любой сдвиг строк с теоремами (так было после восстановления доказательств CC-01) оставляет дерево грязным после verify.sh.
- Сделано: файлы обновлены в 7e41da3, после verify.sh дерево чистое. Предложение: генерировать во временный каталог `$T`.

### INT-07 — два разных способа сборки под тулчейн из исходников
- Модуль: `ffi/build.sh` + `verify.sh` (d8a548b) против `ffi/audit/build_audit.sh` (96ca9aa). Во втором статические libstdc++.a и libsupc++.a копируются под именами libc++.a и libc++abi.a. Класс: BUILD/TEST. Риск 1. P3. CONFIRMED.
- Сделано (TEST-ONLY 8be73ac): оставлен один способ — ffi/build.sh из d8a548b (системные статические libuv/libgmp, пустые заглушки libc++, динамический `-lstdc++`; для Go `CGO_LDFLAGS=-lstdc++`). build_audit.sh вызывает ffi/build.sh.
- Оверлей `lean-overlay-setup.sh` от harness-ci в ветку не входит.
- Повторная проверка: regress.sh, init_race, go test, Go-блок — LOGS/ffi-int-*, LOGS/70, 93.

### INT-08 — расхождение формулировок FORMAT §7 и HOST §1.3; устаревшие числа
- Класс: DOC. Риск 1. P3. CONFIRMED (чтением).
- Правило 3 §7 FORMAT: «вычисляет условия по той же версии снимка». Новый HOST §1.3 требует данных, в которых применены действия прошлых шагов (барьер).
- HOST §7: «шесть видов сбоев» — это PC-17 (13 видов); README уже исправлен в 565a07c, HOST.md — нет.
- Не менялось: нужен владелец текста.

## 5. Commits (audit/integration, по порядку)

Раунд 1. Из других веток (в скобках исходный коммит):
- formats-correctness: 5e32b98 (897bfc8), 224229d (177355a), 71785b8 (4a38b91);
- proofs: 9d25198 (e0091ba), d1defb4 (44f8b39), b733f4f (b5bc7e9), 0ce9a5f (d116a64 — только тест), d381d6e (90201dd), 793d688 (d9934d5);
- python-client: 4121041 (1df3ef8), fff6018 (2550540), ae95128 (bbf7ac7), 52d53af (d8f658b), 31f9eab (51c31ce);
- engine-timers: 815405d (c9b7a1a), afb380e (c6eecd0), 4323143 (8edc9a4), 7730517 (998a539);
- host-perf: 8fdcb4b (c8bb903 без hunk'а harness), 9f9ea35 (1dfe574);
- core-compiler: bc945f0 (7918908), dd5d4e3 (3d73782);
- ffi-go: 4547a2f (96ca9aa), 2385d49 (4998244), 17b0609 (a027905).

Раунд 1. Мои коммиты:
- 22f0203 — test: U4h/U5h;
- 906d3d0 — test: core-compiler, `CC01=fixed`;
- 8134324 — test: гонка в ThreadCount;
- d3782f7 — fix: CC-01 (код, доказательства, документация);
- 7e41da3 — регенерация AllAxioms/theorems;
- 253df6b — test: повтор контроля host_sim.

Раунд 2 (harness-ci):
- 42274fd (db72de1, без client_contract);
- 072a897 (37d0b6f);
- c6a4440 (53a4ffa);
- 56d67c6 (4236416 + слияние verify.sh);
- 92e572b (565a07c);
- e00a311 (b98062f);
- 4b7b3ee (1cd4a74).

Верх ветки (TEST-ONLY):
- 9778dac — = d8a548b, переложен на новый verify.sh;
- 8be73ac — build_audit.

## 6. What I could not check

- **Официальный тулчейн (clang/libc++).** Исправленные исходники им не собирались, поэтому код 0 усиленного verify.sh с настоящим `bin/SHA256SUMS` не получен (INT-01).
- **Семантика CC-01.** Сверена только с прочтением прозы агентом core-compiler: designer-оракул, directed. Риск общей ошибки прочтения остаётся. Доказательство `compile_correct` относится к обоим изменённым определениям и не утверждает «поведение в пределах одной версии не изменилось» — это проверено только тестами.
- **Вариант CC-01 без правки `Engine.missing`** не собирался.
- **host-perf.** `host_model.py` (контрпримеры HP-02…09) и `host_sim_mutants.py` в integration не включены и не перепрогонялись.
- **engine-timers.** Перебор мутантов `ptdriver mutants` на integration не запускался; запускались walk ×4 и suite.
- **ffi-go.** TSan- и ASan-варианты contract/stress (собраны build_audit.sh), `run_contract.sh` и Go soak/scaling/signals не запускались.
- **harness-ci.** Изменённый `.github/workflows/verify.yml` в CI не запускался (нужен push). `verify-faults full` прогнан на 4 из 12 неисправностей плюс `none`.
- **Другие агенты.** formats-security, cross-verify, host-go, host-python не интегрировались: не было коммитов или запроса.
- **Открытые находки, исправлений нет.** CC-02/03/04, ET-04/06, FC-02…05, PC-05…18, FFI-03/04/07/09/10/13, H11 (протокольные мутанты в `Mutate.lean`) — остаточный риск по отчётам агентов.
- **Полнота.** Не утверждаю, что ветка свободна от ошибок. Покрытие — в таблицах выше.

## 7. Раунд 2: harness-ci (7 коммитов)

Коммиты harness-ci вставлены после 253df6b и перед TEST-ONLY коммитами. Авторство сохранено, к каждому добавлено примечание об интеграции.

Пересечения и как они разрешены:

| пересечение | решение |
|---|---|
| (a) Проверка теорем в verify.sh: strict_gate.sh (d381d6e) и ci/theorems.lean (4236416) | Оставлены обе, они дополняют друг друга. ci/theorems.lean сравнивает тексты формулировок 17 теорем README/AUDIT и запрещает `implemented_by`, `@[extern]`, `ofReduceNat`. strict_gate.sh проверяет все 79 теорем исходников с контролем их числа, всё окружение (теоремы, определения, opaque), формулировки 20 теорем через проверку типов, запрещает run_elab/modifyEnv/addDeclCore/unsafe/csimp и запускает leanchecker. THEOREMS.expected совпадает с доказательствами после CC-01: 17 из 17 (LOGS/91); theorems.txt — 7e41da3. |
| (b) client_contract.py ESRCH | Оставлен python-client fff6018 (тот же `ProcessLookupError` плюс закрытие файла); фрагмент из db72de1 отброшен. |
| (c) Порог Go reload | INT-02 = H16, взят 1cd4a74 (4096 КБ). |
| (d) README и AUDIT | 565a07c слился с 793d688 и d3782f7 без текстовых конфликтов, проверено вручную. |
| Конфликт verify.sh (4236416 против 90201dd, 1df3ef8, d8a548b) | За основу взята версия harness-ci. Добавлены: шаг строгой проверки теорем (с обязательной строкой `STRICT GATE: PASS`); `client_robustness.py`; в Go-шаге регрессионные тесты Bot — HelloAlwaysError, RequestBadOp, LoadNUL, RequestNUL, каждый обязан напечатать `--- PASS`. Ожидания подогнаны под фактический вывод: json_fuzz также требует 0 записей, которые Figram не читает обратно (177355a); host_sim — итог по пользователям (c8bb903/1dfe574) и попытки контроля (253df6b); те же два ожидания исправлены в verify-prebuilt.sh. TEST-ONLY `-lstdc++` (d8a548b) переложен на новый шаг C (9778dac). |

Прогоны на HEAD 8be73ac:

| проверка | результат | лог |
|---|---|---|
| `lake build` | ok. Продуктовый Lean не менялся с раунда 1 (`git diff 253df6b HEAD -- '*.lean'` — только ci/theorems.lean); чистая сборка с 0 предупреждений — LOGS/51 | 90-r2-lake-build |
| все теоремы, `#print axioms` | 601, нестандартных 0, как в раунде 1 | 96-r2-all-axioms |
| ci/theorems.lean | 17 теорем, только стандартные аксиомы, формулировки = THEOREMS.expected | 91-r2-ci-theorems, 93 |
| строгая проверка теорем | STRICT GATE: PASS (внутри verify.sh) | 93 |
| усиленный verify.sh без отступления | код 1 на шаге хешей. Причина — тулчейн И изменённые исходники: локальная сборка integration 8051f148/77f2a0c2; локальная сборка baseline b825dac9/f4951ff8; официальная a08c666c/8d96cf27 | 92-r2-verify-sh-no-waiver |
| усиленный verify.sh, `FIGRAM_SHA256SUMS=REPRODUCTIONS/sums/SHA256SUMS.integration-local-gcc` | **код 3**: ровно одно объявленное отступление («хеши сверены с …, соответствие bin/ исходникам не проверено»); все остальные шаги прошли — сборка, обе проверки теорем, golden (12), покрытие 42 из 42 и 36 из 36, 18 103 и 97 282 запроса, мутации 107 из 107, хост, снимки 24, protocol_edges 11 из 11, json_fuzz 30 000 (0 расхождений, 0 нечитаемых), C/ASan, клиент Python (faults, contract, robustness), самопроверка, нагрузка, Go (рост памяти −276 КБ при контроле 50 992 КБ, гонок нет, регрессионные тесты Bot пройдены) | 93-r2-verify-sh-waiver |
| `protocol_edges.py` | движок integration и bin/: 11 из 11, расхождений 0 | 94-r2-protocol_edges-INT, -BIN |
| `verify-faults.sh prebuilt none`, настоящее дерево | код 1 (ожидание 0): json_fuzz — отгруженный `bin/figram` не читает 12 из 8962 записей (FC-01) = INT-01 | faults-prebuilt-realtree |
| `verify-faults.sh prebuilt`, имитация выпуска (`bin/` = локальная сборка HEAD) | none 0; golden-truncated, selftest-shrunk, fixtures-shrunk, random-docs-shrunk, json-fuzz-shrunk, mutants-shrunk, bin-swapped — все 1; все ожидания выполнены | faults-prebuilt-releasesim |
| `verify-faults.sh full` с отступлением, `EXPECT_BASE=3` | none 3; sums-tampered 1; custom-axiom 1 (недопустимая аксиома `figram_cheat` у `guards_irrelevant`); engine-bug-golden-edited 1 (golden.jsonl не совпадает с выводом `figram golden`); go-missing 1; все ожидания выполнены | faults-full-int |

Вердикты раунда 2:

| коммит | вердикт |
|---|---|
| db72de1 (без client_contract) | VERIFIED |
| 37d0b6f | VERIFIED |
| 53a4ffa | VERIFIED; verify.yml — NOT RUN |
| 4236416 (со слиянием) | VERIFIED |
| 565a07c | VERIFIED (DOC) |
| b98062f | VERIFIED |
| 1cd4a74 | VERIFIED (закрывает INT-02) |

Что нужно от процесса выпуска (после раунда 2):
1. Пересобрать `bin/` официальным тулчейном.
2. Обновить `bin/SHA256SUMS` и `VERIFY.log` — с выводом «Происхождение прогона» из нового verify.sh.
3. Не переносить TEST-ONLY коммиты.
4. В CI усиленный verify.sh должен дать код 0. Код 3 в CI считается провалом.

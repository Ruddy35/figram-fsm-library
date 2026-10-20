# fix-final — отчёт интеграции

Ветка `fix/final`, worktree `/home/user/figram-audit-worktrees/fix-final`, HEAD `8e580aa`: 81 коммит над fix/base 8be73ac, из них 7 слияний и 17 коммитов «integration». Ничего не отправлено (push не было), другие ветки и worktree не изменялись.

Логи: `/home/user/figram-audit/fixes/final/LOGS/`, далее `L/`. Бинарники финальной сборки: `/home/user/figram-audit/fixes/final/bin-final/`, stripped sha256:
- figram — `f24daf50…`;
- figram-engine — `f7878550…`.

Скрипты прогонов лежат в `/home/user/figram-audit/fixes/final/`: `perf.sh`, `suites.sh`, `hosts.sh`, `faults.sh`, `client_mutants.sh`.

## 1. Журнал слияний

| # | Слияние | Коммит | Конфликты и решение |
|---|---|---|---|
| 1 | fix/ffi | f32a156 | Нет. TEST-ONLY 9778dac/8be73ac отменены в ветке (9a90583, 189f2ef); вместо них продуктовое B-01 (82ac414). |
| 2 | fix/docs | 05c36e8 | README, таблица «Проверка». Взята строка «C-прослойка» из fix/docs (санитайзеры не видят кучу Lean) с формулировкой fix-ffi «пустой и огромный (5 МБ) запрос». Добавлена строка «C-ABI» из fix-ffi. Оставлена строка «вернуться» из fix/docs (201/2001 вариант). |
| 3 | fix/protocol | 0791c06 | Текстовых нет: verify.sh, README и FORMAT слились автоматически. |
| 4 | fix/core | 7dfc14f | FORMAT §7, правило 3: условие HOST §1.3 из fix-protocol (INT-08) объединено с новой семантикой `need` из fix-core (P-14), добавлена фраза «`need` может быть короче прежнего». `theorems.txt` и `AllAxioms.lean` перегенерированы `gen_axioms.py`: 141 теорема (139 из fix-core и `startAt_zero`, `startAt_snapOK`). Engine.lean, EngineProofs.lean, Main.lean, ptdriver, ptsuite и mutants_red слились автоматически, сборка зелёная. |
| 5 | audit/host-go | 9c3c504 | Нет (добавляет только каталог). |
| 6 | audit/host-python | b87b721 | Нет. |
| 7 | fix/client (после сообщения оркестратора) | e446e53 | HOST.md §5: взят текст fix-client о клиенте, добавлены «различайте ошибки по `code`» и список кодов из fix-protocol/fix-docs. Приём клиентом ответа `{id,error}` без `code` описан как совместимость с движками без кодов; валидаторы `protocol.py` и Go `checkReply` остаются строгими. Абзац о времени загрузки обновлён по новым замерам. HOST.md §7: абзац fix-docs о том, что host_sim не моделирует, плюс check_host_contract; абзац о клиенте сверен с `client_faults.py` (FAKES 13 + START 5). |

Проверка шага 1 брифа: `git diff fix/base~2 fix/final -- ffi/build.sh verify.sh audit/` содержит только продуктовую логику B-01. Последняя адаптация под сборку Lean из исходников — флаги `-luv -lgmp -lstdc++`, жёстко прописанные в `audit/host-go/figram/figram.go`, — удалена (ddc4a87).

### Коммиты «integration»

| Коммит | Суть |
|---|---|
| 7b918de | go-example: `legacyErrorWithoutCode=false`, два новых случая в `TestFixCheckReplyProtocolChanges`. |
| 599e881 | Повторная проверка мутанта через `Machine.wfFast` (`wfFast_eq`). |
| 95e92f4 | README/AUDIT: 13 видов порчи и 37 проверок (check_docs [D-02] был красным); в AUDIT §2 строки про `protocol_edges` и C-ABI. |
| 8d69c90 | check_docs считает FAKES и START отдельно; HOST/README/AUDIT: «13 на запросе и 5 до готовности». |
| e807054 | `contract.c`: запросы для проверки предела размера строгие, без лишнего ключа «pad». |
| 75a49eb | `ci/theorems.lean` и `THEOREMS.expected` 17 → 23. Добавлены `compile_eq_spec`, `wfDirect_eq`, `lint_nil_wf`, `wfFast_eq`, `missing_nil_step`, `startAt_snapOK`; прежние 17 формулировок не изменены. Таблицы теорем README и AUDIT §1 дополнены. |
| bd50fbf | host-python: протокол v1 (см. раздел 4), `suite.py` с HOST_OUT/HOST_LOGS/FIGRAM_ENGINE, `test_protocol_v1.py`. |
| ddc4a87 | host-go: флаги компоновки из B-01 (`scripts/build.sh`), протокол v1, строгий UTF-8, `protocol_v1_test.go`. |
| 4bbc1d0 | Оговорка про double в описаниях `figram.schema.json` и `snapshot.schema.json` (FC-05). |
| 9ba358c | FORMAT §7 и докстринг `Api.request`: ошибки уровня C на пути FFI — с `id: null`. |
| df7dec3 | README «Структура». |
| 87df74b | Шлюз: проверка предупреждений сборки не срабатывает на имя модуля `Figram.Warnings`; новая неисправность `build-warning` в verify-faults. |
| 269f076 | README «После оптимизации» — измеренные числа. |
| 097c924 | `ffi/audit/tsan.supp` и текст AUDIT §2. |
| dd6d37f | RED-матрица host-go после исправлений продукта. |
| 8e580aa | Комментарии хостов: «start with seq». |

## 2. Статус пунктов аудита

Сокращения: «база» — исправлено ещё в fix/base; S — `L/suites/`; G — `L/gate/`; H — `L/hosts/`.

| Пункт | Статус | Коммиты на fix/final | Тест | Лог |
|---|---|---|---|---|
| P-01 | FIXED | 0791c06 (d4befe9, `startAt`); 05c36e8 (HOST §4); bd50fbf, ddc4a87 | `protocol_edges` (start seq); `check_host_contract` (`start` с верхней отметкой); `test_protocol_v1.py`; Go `TestEngineCodesAndStartSeq`, `TestResetContinuesHighWaterSeq`; в хостах сбросы и ремонты идут через `start` с `seq`, инвариант `seq_plus_one` | S/63, S/23, H/12, H/15, H/python/00, H/go/01 |
| P-02 | FIXED-DOC (база 8fdcb4b) | — | `host_sim` и контроль `--no-barrier` | S/65, S/66 |
| P-03 | FIXED-DOC (база) | — | HOST §1.5, check_docs | S/06 |
| P-04 | FIXED (база d3782f7) | — | cc designer-upgrades; host-go migration-1 | S/10, H/go/02 |
| P-05 | FIXED-DOC | 05c36e8 (3f9a08d, deda19e) | check_docs [P-05], `check_host_contract` | S/06, S/23 |
| P-06 | FIXED | 7dfc14f (b6cba4f, 4cfc6a6) | `perf_scale --max-load-s 5` | L/perf/01 |
| P-07 | FIXED | 7dfc14f (b6cba4f, 2024513) | `perf_scale --max-step-us 1000` (global_big: 24,3 мс → 0,14 мс) | L/perf/01 |
| P-08 | FIXED (база 2385d49) | — | regress FFI-01, гонка init ×300 | S/41 |
| P-09 | FIXED | f32a156 (b7f7476) | regress P-09 ×4; Go `TestFixInitUnderRlimitAS` | S/41, G/02 |
| P-10 | FIXED | 0791c06 (653be32) | `f_parser_scaling --check`; `f_parser_diff` 60 000 случаев, расхождений 0 | L/perf/03, S/30, S/31 |
| P-11 | FIXED-DOC | 05c36e8 (3f9a08d), база 8fdcb4b | check_docs PH-03, PH-05, CV-C7, D-06; RED no-resource-lock | S/06, H/python/02 |
| P-12, P-13 | FIXED (база 71785b8) | — | json_fuzz, formats e-cli | G/02, S/30 |
| P-14 | FIXED | 7dfc14f (c89aef2) | directed H3; ptsuite N2/N6/N7; теорема `missing_nil_step` | S/10, S/20 |
| P-15 | FIXED-DOC, плюс предупреждение ET-05 | 05c36e8 (3f9a08d, b81b0b7); 7dfc14f (10c5d14) | check_docs real ET-05/ET-06; lint_warnings | S/06, S/11 |
| P-16 | FIXED | 0791c06 (d4befe9, 60fe914) | `protocol_edges`; `d_engine_requests`; R12 | S/63, S/30, S/60 |
| P-17 | FIXED | 0791c06 (d4befe9, db974ec); 599e881 | ptsuite M1/M2 | S/20 |
| P-18 | FIXED | 0791c06 (процесс); f32a156 (FFI); ddc4a87 | `protocol_edges` u-*; regress utf8_strict; Go `TestInvalidUTF8IsRejected` | S/63, S/41, S/52 |
| P-19 | FIXED | 0791c06 (d4befe9); e807054 | `protocol_edges`, `d_engine` | S/63, S/30 |
| P-20 | FIXED | 0791c06 (7aeeaf8, d4befe9); 7b918de, bd50fbf, ddc4a87 | `protocol_edges`; Go `checkReply`; тесты хостов | S/63, S/50, H/12, H/15 |
| P-21 | FIXED (остаточное UB описано) | f32a156 (baf6ce6, b7f7476) | regress 24/24; sanitizers 20/20; ffi `mutants.sh` | S/41, S/42-GREEN, S/43 |
| P-22 | FIXED (+DOC) | f32a156 (b7f7476, 5a87c81) | regress signals | S/41 |
| P-23 | FIXED-DOC | f32a156 (5a87c81) | `doc_check.sh` | S/03 |
| P-24 | FIXED (CC-05, ET-05, FC-07) и FIXED-DOC (CC-06/07/08, FC-05, FC-02) | 7dfc14f (10c5d14); 05c36e8 (b81b0b7); 4bbc1d0 | lint_warnings 16/16; lint_gaps; check_docs real CC-06/07/08, FC-05 | S/11, S/10, S/06 |
| P-25 | BY-DESIGN (лемма о протоколе доказана, текст D-04) | база (Glue); 05c36e8 | `GlueAxioms`: `timer_key_needs_token` | L/proofs-extra/02-GlueAxioms |
| I-01 | FIXED | e446e53 (aba32d1) | R10 ×8 | S/60 |
| I-02 | FIXED (база 31f9eab) | — | R5 | S/60 |
| I-03 | FIXED | e446e53; 0791c06 (эхо id, FIFO) | R12 | S/60 |
| I-04 | FIXED; остаток — ограничить повторы (HOST §5) | e446e53 (aba32d1, 28fd8c6) | R4, R11 | S/60 |
| I-05, I-06 | FIXED (база) | — | R6, R7 | S/60 |
| I-07 | FIXED | e446e53 | R13 | S/60 |
| I-08 | FIXED-DOC | e446e53 | R14 | S/60 |
| I-09 | FIXED (база) | — | R9 | S/60 |
| I-10, I-11, I-12, I-13 | FIXED | e446e53 | R15, R16, R17, R18 | S/60 |
| I-14, I-15, I-16 | FIXED (база; I-15 сохранено fix-ffi) | — | Go `TestAudit*` | G/02 |
| I-17 | FIXED | f32a156 (5238930) | `TestFixLazyLoadWithoutInit` | G/02, S/50 |
| I-18 | FIXED (база) | — | `host_sim`, `--no-barrier` | S/65, S/66 |
| T-01 | FIXED (база) | — | verify-faults golden-truncated, sums-tampered — красные | L/faults/full-* |
| T-02 | FIXED (база) | — | bin-swapped — красный | L/faults |
| T-03 | FIXED (база); в ci закреплено 23 теоремы (75a49eb) | 75a49eb | custom-axiom, native-decide, theorem-weakened — красные; strict_gate PASS | L/faults, S/05 |
| T-04 | FIXED (база) | — | `protocol_edges`, 106 случаев | S/63 |
| T-05 | FIXED | база + e446e53 | мутации клиента: убито 47 из 49 (M18 и M47 эквивалентны) | L/client-mutants/B1–B8 |
| T-06 | FIXED (база) | — | `host_sim --no-barrier` — красный | S/66 |
| T-07 | FIXED (база) | — | go-missing, race-test-not-run — красные | L/faults |
| T-08 | FIXED (база) | — | 6 неисправностей *-shrunk в prebuilt — красные | L/faults/prebuilt-* |
| T-09 | FIXED (база) | — | coverage-drop-regen — красный | L/faults |
| T-10 | FIXED (база) | — | selftest 37; контроль json_fuzz | G/02 |
| T-11 | FIXED (база); см. риск о R4 | — | — | L/05-verify-prelim2-client_robustness-R4-flake.log |
| T-12 | FIXED-DOC, плюс подавление TSan | f32a156, 05c36e8, 097c924 | sanitizers 20/20; контрольная гонка ловится | S/42-GREEN |
| T-13 | FIXED | 7dfc14f (700ca02, 42af993) | cc mutants: 2517 мутантов, 1488 убиты, 1029 эквивалентны, неэквивалентных выживших 0 | S/cc/mutants.log |
| T-14 | FIXED | 05c36e8 (accc4a1, 583c88d) | `test_strict_gate_clean.sh`: 0 сбоев, дерево чистое | S/04, S/05 |
| T-15 | PARTIAL (база 53a4ffa; `elan-init.sh` по-прежнему из master через `curl \| sh`; CI не запускался) | — | — | — |
| T-16 | FIXED (база) | — | — | G/02 |
| B-01 | FIXED (официальная раскладка проверится только в CI) | f32a156 (82ac414), ddc4a87 | шлюз без `CGO_LDFLAGS` — код 3; `b01_build.sh` | G/02, S/44 |
| B-02 | NOT-FIXED по правилам (`bin/` не пересобирать) | — | `verify-prebuilt` на тестовой копии с новыми `bin/` — код 0 | L/faults/prebuilt-tree-verify-prebuilt |
| D-01 | FIXED-DOC (`VERIFY.log` перегенерирует оркестратор) | 05c36e8 | check_docs | S/06 |
| D-02 | FIXED-DOC | 05c36e8, 95e92f4, 8d69c90, 269f076 | check_docs | S/06 |
| D-03, D-04 | FIXED-DOC | 05c36e8, 75a49eb | check_docs | S/06 |
| D-05 | FIXED-DOC | f32a156, 05c36e8 | `doc_check.sh`, check_docs | S/03, S/06 |
| D-06 | FIXED-DOC | 0791c06 (FORMAT §7 п. 3); 05c36e8, e446e53, 8d69c90 (HOST §7: 13 + 5) | check_docs, `g_protocol_docs` | S/06, S/02 |
| D-07, D-08 | FIXED-DOC | 05c36e8 | check_docs real CC-06, CC-07 | S/06 |

### Новые дефекты, найденные при интеграции

| № | Дефект | Исправление | RED-лог | GREEN-лог |
|---|---|---|---|---|
| N-1 | `verify.sh` на чистой сборке (а значит, в CI) красный: `grep -qi warning` совпадает со строкой «Built Figram.Warnings» | 87df74b | G… `L/21-RED-verify-clean-build-warning-grep` | `L/gate/01`, `L/gate/02`; неисправность build-warning ловится |
| N-2 | `contract.c` шлёт лишний ключ «pad», строгие запросы его отвергают | e807054 | `L/04-verify-prelim` | шлюз |
| N-3 | check_docs [D-02] и неверный подсчёт видов сбоев (18 против 13 + 5) | 95e92f4, 8d69c90 | `L/02-check_docs-after-merge` | S/06 |
| N-4 | host-go: жёстко прописанные флаги сборки из исходников | ddc4a87 | — | — |
| N-5 | Хосты отвергали `{id,error,code}`; Python-хост чинил снимок по слову «снимок» в тексте | bd50fbf, ddc4a87 | H/11, H/13 | H/12, H/15 |
| N-6 | TSan: ложная гонка на `lean_obj_once` (sanitizers красный в 2 из 5 прогонов) | 097c924 | S/42-RED | S/42-GREEN |
| N-7 | В RED-матрице Go две строки позеленели после P-04 и P-21 | dd6d37f | `H/go/first-run-red/` | H/go/02, код 0 |
| N-8 | Новые теоремы, на которые ссылаются README и FORMAT, не были закреплены в `ci/` | 75a49eb | — | G/02 (23 формулировки) |

## 3. Проверки шага 3

| Команда | Код | Лог | Что доказывает |
|---|---|---|---|
| `rm -rf .lake/build; lake build` | 0 | G/01-clean-lake-build | Чистая сборка: 46 задач, 0 предупреждений, 0 ошибок. |
| `FIGRAM_SHA256SUMS=… ./verify.sh` без `CGO_LDFLAGS`, коммит 269f076 | 3 (только отступление по хешам) | G/02-verify-full (+ `02-verify-logs/`) | Весь шлюз: 23 закреплённые формулировки; strict_gate PASS на 141 теореме; golden; 100 случайных документов; 107/107 мутаций; host_sim; C/ASan; C-ABI 24; клиент; selftest 37; нагрузка; Go с `-race`, RLIMIT_AS, перезагрузки. B-01 больше не требует `CGO_LDFLAGS`. |
| `verify-faults.sh full` — 13 прогонов по одной неисправности | none = 3, остальные 12 = 1 | L/faults/full-*, `full/*/summary.txt` | Каждая неисправность делает шлюз красным, включая новую build-warning; прогон none — полный verify на dd6d37f. |
| `verify-faults.sh prebuilt` на тестовой копии с новыми `bin/` | none = 0, 7 неисправностей = 1 | L/faults/prebuilt-* | Шлюз без Lean чувствителен ко всем неисправностям. |
| `verify-prebuilt.sh` на той же копии | 0 | L/faults/prebuilt-tree-verify-prebuilt | Путь prebuilt зелёный, когда `bin/` пересобран. |
| `audit/proofs/strict_gate.sh` и `test_strict_gate_clean.sh` | 0 / 0 | S/05, S/04 | 141 теорема, только стандартные аксиомы, leanchecker; дерево остаётся чистым. |
| `lake build FigramAudit`; `GlueAxioms`, `Counterexamples`, `TextRoundTrip` | 0 | L/proofs-extra/ | Доказательства аудита собираются. |
| `CounterexamplesNegative`, `StatementsNegative` | 1 / 1 | L/proofs-extra/ | Ровно 4 и 2 ожидаемые ошибки — отрицательные контроли работают. |
| core-compiler `run_all.sh` | 0 | S/10, S/cc/ | 26 из 26 шагов; mutants: 0 неэквивалентных выживших, то есть 490 выживших из fix/base устранены. |
| core-compiler: lint_warnings, check_messages `--fc07`, compile_same | 0 / 0 / 0 | S/11–13 | 16/16; 505 документов без расхождений; 62 документа и 34 374 мутанта без расхождений. |
| engine_timers: suite, walk ×4, mutants ×2, `check_host_contract` | все 0 | S/20–23 | 57 сценариев, 0 провалов; мутанты убиты 56/56 и 51/51; процедура `start` с `seq` выполнена. |
| `tests/formats/run_all.sh` и `f_parser_diff` | 0 / 0 | S/30, S/31 | Все шаги PASS, включая f/g; 60 000 входов без расхождений. |
| `ffi/audit`: build_audit, regress, sanitizers, mutants, b01_build | все 0 | S/40–44 | 24/24; 20/20; все мутанты прослойки пойманы; файлы компилируются без системных заголовков. |
| `go test -race ./...`: go-example, host-go; без `-race` — go-example | 0 | S/50–53 | PASS. |
| client_robustness, client_faults, client_contract, protocol_edges, loadtest, host_sim | все 0 | S/60–65 | 54 / 20 / 10 ok; 106 случаев; 8000 шагов без расхождений; контракт выполнен. |
| `host_sim --no-barrier` | 1 (ожидаемо) | S/66 | Красный контроль срабатывает. |
| `mutate_client_v2.py`, 8 партий | 0 | L/client-mutants/ | Убито 47 из 49; M18 и M47 эквивалентны, как у fix-client. |
| check_docs, g_protocol_docs, doc_check (HEAD) | 0 | S/01–03, S/06 | Документация согласована с кодом. |
| `perf_scale` с порогами; `f_parser_scaling`; бенчмарк pizza | 0 | L/perf/ | Числа для README «После оптимизации». |
| host-python: `suite.py ok`, `red`, `matrix` | 0 | H/python/ | 15/15, все инварианты PASS (9 ремонтов и 6 сбросов через `start` с `seq`); 30 RED-прогонов; ни одного инварианта, который ни разу не был красным. |
| host-go: build, `run_all`, `red_matrix`, compare | 0 / 0 / 0 / 0 | H/go/ | 15/15 PASS; 30 строк, пропусков 0, все инварианты хотя бы раз красные; Go против Python — 15 сценариев, необъяснённых различий 0 (booking: только победители гонки). |
| `figram golden` в отдельный каталог и `cmp` | 0 | G/03 | golden.jsonl, pizza и features побайтно совпадают. |

## 4. Golden, изменения поведения, риски

**Golden.** Перегенерирован `figram golden`: все три файла побайтно совпадают с `examples/`. Ни одна ветка golden не меняла. Новые правила `need`, коды ошибок и строгие запросы не затрагивают 12 фиксированных сценариев.

**Изменения, видимые хостам** (протокол остаётся версии 1):
- Протокол движка:
  - ответ-ошибка — ровно `{id, error, code}`, коды `parse_error`, `too_large`, `bad_request`, `bad_snapshot`, `seq_overflow`; тексты ошибок новые;
  - при ошибке разбора `id` берётся из начала строки; ответы гарантированно идут по порядку запросов (FIFO);
  - запросы строгие: лишнее поле, `token` у обычного события или вне 0…2^53−1, пустой `event`, пустое имя таймера — `bad_request`;
  - `start` принимает `seq`;
  - предел запроса 1 МиБ (`--max-request`, `figram_set_max_request`);
  - невалидный UTF-8 — `parse_error`, без замены на U+FFFD;
  - мутанты доступны только при `FIGRAM_ALLOW_MUTANTS=1`;
  - `need` может быть короче прежнего (P-14).
- Документ: `figram check` печатает предупреждения в stderr с кодом 0; корень называется «bot» (FC-07).
- C-ABI:
  - инициализация ленивая и потокобезопасная, коды init 0/1/2;
  - NULL и неверные дескрипторы безопасны; есть варианты `_n` и `load_doc_ex`;
  - SIGPIPE и `sigaltstack` восстанавливаются;
  - флаги компоновки — в `ffi/out/LDFLAGS`;
  - отказы уровня C приходят с `id: null`.
- Клиент: рукопожатие hello, ожидание загрузки в конструкторе, таймауты по вызову, FIFO, `EngineError.code`, `retryable`, `start(seq)`.
- Скорость: загрузка почти линейная, большие документы в 100–1000 раз быстрее (global_big: 195 с → 1,0 с), шаг на миллионе строк — 24 мс → 0,14 мс. На маленьком документе шаг подорожал на 5–9 мкс CPU из-за побайтного чтения: pizza 24–28 мкс против 19–20 мкс на fix/base; в README указаны измеренные числа.

**Риски:**
- Раскладка официального тулчейна (libc++) локально не проверена; проверит первый прогон CI.
- `bin/` и `VERIFY.log` должен пересобрать оркестратор: `verify-prebuilt` на `bin/` из git красный по замыслу.
- Шаг остаётся O(сообщений): flat_4000 — около 0,5 мс.
- Один невоспроизводимый сбой R4 SIGSTOP в client_robustness (2 ошибки из 4) на втором предварительном verify. Не повторился ни в 3 отдельных прогонах, ни в 15 пробах под нагрузкой CPU, ни в финальном шлюзе, ни в прогоне `none` из verify-faults, ни в наборах.
- Подавление TSan покрывает все отчёты, где в стеке есть инициализатор `_init_lp_figram_*`.
- В C-ABI остаётся UB при выгрузке одновременно с вызовом внутри C: soak с перезагрузкой каждые 2 мс по-прежнему роняет хост, который не ждёт вызовов.
- Нехватка памяти во время работы по-прежнему приводит к INTERNAL PANIC.
- T-15: `elan-init.sh` берётся из master.

**Не сделано:**
- Экспорт `Engine.handleBytes` для FFI: сменил бы контрактные тесты C и Go ради эха `id` у трёх отказов уровня C. Асимметрия задокументирована в FORMAT §7 и `Api.lean`.
- Отчёты аудита в `audit-final/` (PRODUCTION_*.md) не правил: они в другом дереве.
- Вывод первого прогона RED-матрицы Go перезаписан повторным прогоном; доказательные файлы для 200 мс сохранены в `H/go/first-run-red/`.

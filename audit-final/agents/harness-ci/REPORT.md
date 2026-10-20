# harness-ci — аудит тестового стенда Figram (verify-скрипты, обвязка, CI, журналы)

Базовая версия `d69bbaf` (audit/baseline), ветка `audit/harness-ci`, каталог
`/home/user/figram-audit/agents/harness-ci/` (`LOGS/`, `REPRODUCTIONS/`).

Какие бинарники в журналах:
* **bin (CI-verified)** — `bin/figram*` из архива. CI-прогон 39330382243 на официальном Lean 4.35.0
  пересобрал их побайтно (`/home/user/figram-audit/LOGS/10-ci-verify-run-39330382243.log`, мой
  конспект — `LOGS/ci-github-runs.txt`). Журналы с суффиксом `-PREBUILT`/`-bin` относятся к ним.
* **local** — сборка из исходников локальным тулчейном (Lean 4.35.0 из исходников, gcc). Для частей C и Go
  нужен оверлей `REPRODUCTIONS/lean-overlay-setup.sh`: `lean --print-prefix` отдаёт каталог, где
  `libc++.a` — линкер-скрипт `INPUT(-lstdc++)`, а `libuv`/`libgmp` взяты из `fresh/ffi-out`. Скрипты при этом не меняются.

## 1. Scope & contract

Объект: `verify.sh`, `verify-prebuilt.sh`, `ffi/python-example/{conformance,mutation,random_docs,
snapshot_fixtures,json_fuzz,harness_selftest,loadtest}.py`, `Figram/{Mutate,Trace,RandomDoc}.lean`,
`examples/golden.jsonl`, `.github/workflows/*.yml`, `VERIFY.log`, утверждения README («Проверка»),
AUDIT.md §2, REVIEW.md. Классы TEST / BUILD / DOC.

Проверяемый контракт: «`verify.sh` завершился с кодом 0 ⇒ каждое утверждение раздела "Проверка"
выполнено для этих исходников». Как проверял: внедрял неисправности в копии дерева и запускал настоящие
скрипты, старые и усиленные (`verify-faults.sh`). Шлюз обязан покраснеть на каждой неисправности.
Силу тестов измерял 20 мутантами движка вне `Mutate.lean` и матрицей «кто их ловит».

## 2. Guarantee table

| guarantee | test | oracle | actually run? | coverage | gap |
|---|---|---|---|---|---|
| Сборка без предупреждений | `verify.sh`, шаг «Сборка» | `grep warning` | да: CI; local (`LOGS/local-lake-build-worktree.log`) | 42 задачи | нет: Lake повторяет предупреждения уже собранных модулей (`LOGS/r11_lake_warning_replay.log`) |
| `bin/` собраны из этих исходников | «Приложенные бинарники» | sha256 пересборки против `bin/SHA256SUMS` | CI: совпало; local: расходится (другой тулчейн) | 2 файла | расхождение НЕ фатально (H01); сами `bin/*` не сверяются (H02) |
| Только стандартные аксиомы, без `sorry` | `#print axioms` + `grep sorry` | grep | да | 14 из 17 теорем из README/AUDIT | собственная аксиома, `native_decide`, ослабленная формулировка проходят (H03) |
| Сохранённые сценарии совпадают с эталоном | `cmp golden.jsonl` | `figram golden` | да | 12 сценариев | расхождение НЕ фатально (H01); файлы документов примеров не сравниваются |
| Движок совпадает с эталонной семантикой | `conformance.py` (golden + `trace 200 40 7`) | `Chart.step` | да | 18 103 запроса | покрытие не проверяется (H05); эталон делит код с движком (H11) |
| 100 случайных документов | `random_docs.py` | то же | да | 97 282 запроса | печатался аргумент, а не факт (H06) |
| Сила тестов | `mutation.py` | — | да | 107 мутантов таблиц автомата | протокол, снимок, `need`, жетоны, `restart`, `hello` не мутируются (H11) |
| Контракт хоста | `host_sim.py` | учёт заказов | да | 30 пользователей, 140 сбоев | — |
| CLI, огромный показатель степени | шаги CLI | код выхода, stderr, время | да | 4 + 1 | — |
| Снимок: схема и движок согласованы, граница `seq` | `snapshot_fixtures.py` | jsonschema | да | 24 снимка + 3 запроса | граница `seq` в `restart` не проверялась (H11) |
| Текстовый JSON | `json_fuzz.py` | `json` Python + ограничения | да | 30 000 случаев | NaN/Infinity не генерировались (H09); контроль `lax` засчитывал падение (H07) |
| C-прослойка | `c_harness`, ASan/UBSan | самосогласованность | CI; local с оверлеем | 16 потоков × 20 000 | диагностика компилятора скрыта (H15) |
| Клиент Python | `client_faults/contract.py` | — | да | 13 видов сбоев | нестабилен (H10) |
| Обвязка ловит порчу | `harness_selftest.py` | — | да | 35 проверок | «распознана» выродилось (H08); число не контролировалось (H06) |
| Нагрузка | `loadtest.py` | сценарии | да | 8000 шагов | `recovered` не сравнивается (P3, не исправлял) |
| Go | `gofig`, `go test -race` | сценарии | CI; local с оверлеем | 600 + 60 горутин | без `go` раздел пропускается; тест гонки мог не выполниться (H04); тест памяти нестабилен (H16) |
| Журнал прогона | `VERIFY.log` | — | — | — | снят до текущих скриптов (H12) |

## 3. Findings

Общее: путь в продукт косвенный. Дефект стенда позволяет выпустить дефект продукта с зелёной
проверкой; риск оценён для шлюза качества. Базовая версия везде `d69bbaf`.

### H01 — расхождение хешей `bin/*` и golden-сценариев не останавливает проверку (TEST, P2, Risk 6, CONFIRMED)
* module: `verify.sh:49,75`, `verify-prebuilt.sh:55`. Путь: шлюз выпуска («код 0 доказывает
  соответствие `bin/` исходникам и регрессионному оракулу»).
* trigger: `(…) | diff - bin/SHA256SUMS && echo ok` и `cmp … && echo ok` при `set -euo pipefail`. Неуспех
  левой части списка `&&` bash не завершает (`LOGS/r01b_constructs.log` — разбор всех конструкций в обоих скриптах;
  кроме этих двух, не фатален только цикл `cp && strip`, чей отказ всё равно маскируется тем же `diff`).
* impact: печатается diff или сообщение cmp, затем «ВСЁ В ПОРЯДКЕ», код 0. Сквозной сценарий `engine-bug-golden-edited`:
  1. движок перестаёт выставлять `recovered` (`Engine.result`);
  2. `golden.jsonl` правят под изменённый движок;
  3. `bin/` пересобирают.

  Базовый `verify.sh` печатает `… golden.jsonl differ: byte 8274, line 12`, а затем «ВСЁ В ПОРЯДКЕ», код 0
  (`LOGS/faults-full-baseline-4/full-engine-bug-golden-edited.log`).
* likelihood: высокая. Срабатывает на любом расхождении, сообщение теряется в выводе; на локальном
  gcc-тулчейне хеши расходятся всегда.
* repro и expected / actual / exit code:
  * `bash REPRODUCTIONS/r01_andlist_extract.sh` — выдержка из скрипта, код 0 при расхождении;
  * `bash REPRODUCTIONS/r03_golden_truncated_green.sh --quick` — настоящий `verify-prebuilt.sh` с golden, усечённым до 2 из 12 сценариев: «ПРОЙДЕНА», код 0
    (`LOGS/r03_golden_truncated_green-quick.log`, bin CI-verified);
  * полный базовый `verify.sh` на local: diff хешей и «ВСЁ В ПОРЯДКЕ», код 0 (`LOGS/old-verify-sh-local-baseline.log`).
* fix: `if ! …; then fail; fi`; `figram golden` дополнительно сверяется с `pizza.bot.json` и `features.bot.json`;
  `shopt -s inherit_errexit`. Commits `37d0b6f`, `4236416`.
* regression / retest: `verify-faults.sh` — golden-truncated, sums-tampered, engine-bug-golden-edited дают код 1
  (`LOGS/faults-prebuilt-hardened-final/`, `LOGS/faults-full-hardened-1/`, `LOGS/faults-full-hardened-4/`).

### H02 — `verify.sh` не сверяет сами `bin/*` с `bin/SHA256SUMS` (TEST, P2, Risk 5, CONFIRMED)
* `verify.sh` сравнивает пересборку только с файлом хешей. `bin/figram*` он не читает: `grep bin/` по скрипту
  находит лишь `bin/SHA256SUMS`. Подменённый `bin/figram-engine` с нетронутым `SHA256SUMS` проходит при любом
  тулчейне, даже если H01 исправлен. `verify-prebuilt.sh` сверяет `bin/` только с тем же файлом из архива.
* repro: `GATE_TREE=<базовое дерево> ./verify-faults.sh full bin-swapped`. Новый шлюз: код 1, «bin/* не совпадают с bin/SHA256SUMS»
  (`LOGS/faults-full-hardened-1/full-bin-swapped.log`).
* fix: `(cd bin && sha256sum --quiet -c SHA256SUMS)` выполняется всегда. Commit `4236416`.

### H03 — шлюз аксиом сводится к `grep sorry`; собственные аксиомы, `native_decide` и ослабление формулировок проходят (TEST, P2, Risk 6, CONFIRMED)
* module: `verify.sh:51–71`. Путь: утверждение README/AUDIT «доказано, только стандартные аксиомы».
* Прогоны базового `verify.sh` целиком (local), каждый кончился «ВСЁ В ПОРЯДКЕ», код 0:
  * `axiom figram_cheat …` и `step_guard_local := figram_cheat …`: скрипт сам печатает
    `'Figram.Machine.step_guard_local' depends on axioms: [propext, Machine.figram_cheat]` (`LOGS/faults-full-baseline-1/full-custom-axiom.log`);
  * `example … := by native_decide` (`LOGS/faults-full-baseline-2/full-native-decide.log`);
  * посылка `(_hfalse : False)` в `reply_readable`: доказательство то же, предупреждений нет (`…-baseline-1/full-theorem-weakened.log`).
* `run_correct`, `toChart_decls`, `guards_irrelevant` упомянуты как доказанные, но не проверялись вовсе. Сейчас у них
  только стандартные аксиомы, у `toChart_decls` аксиом нет совсем (`LOGS/local-theorems-lean.log`).
* likelihood: средняя (временная аксиома «на потом», `native_decide` ради скорости).
* fix (`4236416`):
  * `ci/theorems.lean` — все 17 теорем;
  * белый список `{propext, Classical.choice, Quot.sound}`;
  * формулировки закреплены в `ci/THEOREMS.expected` (вывод pp без переносов строк);
  * поиск в исходниках `axiom|native_decide|implemented_by|@[extern|sorry|admit|skipKernelTC|ofReduceBool`.
* retest: custom-axiom → код 1 («недопустимые аксиомы ['Figram.Machine.figram_cheat']» у `step_guard_local` и у
  наследующей её `guards_irrelevant`); native-decide → 1; theorem-weakened → 1 (`LOGS/faults-full-hardened-1/`).
* Остаток: `partial def` (11 штук, парсер и печать JSON) и компилятор Lean вне доказательств, как и заявлено в AUDIT.

### H04 — пропуски: Go молча пропускается, тест гонки может не выполниться, `--quick` давал код 0 (TEST, P3, Risk 3, CONFIRMED)
* go-missing: без `go` в PATH базовый `verify.sh` не печатает раздела Go и заканчивает «ВСЁ В ПОРЯДКЕ», код 0
  (`LOGS/faults-full-baseline-2/full-go-missing.log`, строк `== Go` — 0).
* race-test-not-run: `TestSharedRequestMap` переименован. `go test` проходит без тестов, скрипт печатает «общий словарь
  запроса из 8 горутин: гонок нет», код 0 (`LOGS/faults-full-baseline-3/`).
* `--quick` пропускает мутации и самопроверку, код 0. Сообщение честное, но CI его от полной проверки не отличит.
* fix (`4236416`, `37d0b6f`): Go обязателен (`FIGRAM_WAIVE=go` даёт код 3 и список отступлений); `go test -v -run`
  с обязательной строкой `--- PASS`; `--quick` даёт код 3. retest: go-missing → 1, race-test-not-run → 1 (`LOGS/faults-full-hardened-2/`).

### H05 — покрытие «42 из 42 / 36 из 36» печатается, но не проверяется (TEST, P3, Risk 2, CONFIRMED с оговоркой)
* Испорченный генератор (`roll < 23` → `< 88`) даёт покрытие 36/42 и 35/36. Базовый шлюз ловит это только косвенно:
  мутаций обнаружено 100 из 107, выживают `redirect:*` (`LOGS/r12_coverage_vs_mutation-88.log`). При `< 97`
  — то же, отказ на шаге мутаций (`LOGS/faults-full-baseline-3/`). В `--quick` мутаций нет, падение покрытия не видно.
* fix: строка «A из B» с A = B > 0 обязательна. retest: coverage-drop-regen → 1 на шаге покрытия (`LOGS/faults-full-hardened-3/`).

### H06 — нет нижних границ числа проверок; `random_docs.py` печатал аргумент (TEST, P3, Risk 4, CONFIRMED)
* Каждая «усадка» оставляет базовый `verify-prebuilt.sh` зелёным, код 0 (bin CI-verified,
  `LOGS/faults-prebuilt-baseline-{1,2,3}/summary.txt`):
  * мутаций 2 из 2 вместо 107 (`MIN_MUTANTS = 1`);
  * самопроверка 10 проверок вместо 35;
  * 2 снимка вместо 24;
  * 10 случаев фаззинга вместо 30 000;
  * 1 документ вместо 100, при этом выводилось «документов 100».
* fix (`db72de1`, `37d0b6f`, `4236416`): шлюз разбирает итоговые строки и требует:
  * мутаций ≥ 107, и все убиты;
  * самопроверок ≥ 35 и 0 строк «НЕТ»;
  * снимков ≥ 24;
  * случаев 30 000 = принято + отвергнуто;
  * документов ровно 100.

  `random_docs.py` печатает и проверяет фактическое число. retest: все пять дают код 1 (`LOGS/faults-prebuilt-hardened-final/`).

### H07 — контроль чувствительности фаззера засчитывает любое падение (TEST, P3, Risk 2, CONFIRMED)
* Если `lax.py` нельзя исполнить (например, `/tmp` с `noexec`), `json_fuzz.py` падает с `PermissionError`, а контроль
  печатает «обнаружен» (`REPRODUCTIONS/r06_lax_control_vacuous.sh`, `LOGS/r06_lax_control_vacuous.log`).
* Насколько контроль осмыслен: «парсер без ограничений» ловится (96 расхождений из 3000), но вклад каждого
  ограничения по отдельности он не показывает. Покомпонентная проверка — в H09.
* fix (`4236416`): нужен код ровно 1, строка «РАСХОЖДЕНИЕ» и отсутствие Traceback.

### H08 — в самопроверке обвязки «распознана» выродилось в «нет Traceback» (TEST, P3, Risk 2, CONFIRMED)
* `DETECTED` в `harness_selftest.py` совпадает с итоговыми строками успеха всех трёх инструментов («расхождений 0»,
  «ответов 24»). `REPRODUCTIONS/r07_selftest_regex_vacuous.py`: до исправления совпадает в 3 из 3, после — в 0 из 3
  (`LOGS/r07_selftest_regex_vacuous-{baseline,fixed}.log`).
* fix (`db72de1`): засчитываются только сообщения о нарушении. Самопроверка проходит 35 из 35 (`LOGS/selftest-fixed-regex-bin.log`).

### H09 — фаззер JSON не порождает NaN/Infinity (TEST, P3, Risk 2, CONFIRMED)
* Модельный парсер, точно повторяющий оракул, даёт 0 расхождений. Если убрать любое одно ограничение — фаззер
  красный, кроме NaN/Infinity: парсер, который их принимает, проходит 30 000 случаев (`REPRODUCTIONS/r05/run.sh`,
  `LOGS/r05_jsonfuzz_sensitivity-baseline.log`). Сам `figram json` NaN/Infinity отвергает (bin CI-verified),
  дефекта продукта нет.
* fix (`db72de1`): генератор порождает и `NaN`, `Infinity`, `-Infinity`. retest: все 5 ослаблений красные (`…-fixed.log`).

### H10 — `client_contract.py` нестабилен (TEST/flaky, P3, Risk 2, CONFIRMED; возможно пересечение с python-client)
* `state()` ловит только `FileNotFoundError`. Если процесс собран между `open()` и `read()` файла `/proc/PID/stat`,
  вылетает `ProcessLookupError`: 4–5 отказов из 20 (`LOGS/r02_client_contract_flaky-PREBUILT.log`).
  Мой первый прогон `verify-prebuilt.sh --quick` упал именно на этом.
* fix (`db72de1`): ловить и `ProcessLookupError`. retest: 0 отказов из 30 (`LOGS/r02_client_contract_flaky-fixed.log`).

### H11 — мутации не касаются протокола движка; 6 из 20 мутантов движка проходят весь базовый шлюз (TEST, P2, Risk 5, CONFIRMED)
* `Mutate.lean` мутирует только `rows/entry/exit/resumes` скомпилированного автомата. Мои 20 мутантов — в
  `Core.lean`/`Engine.lean` (`REPRODUCTIONS/r10_engine_mutants/`, `LOGS/r10/matrix.tsv`, local).
* Как прогонял: для каждого мутанта полный `lake build` (доказательства, `#guard`, линтер), затем отдельно
  `lake build figram-engine`. Оракул `figram` — мутированный, если библиотека собралась, иначе bin (CI-verified).
  Контроль `none` даёт 0 во всех столбцах.
* Столбцы: BUILD — доказательства; GC — `figram golden` ≠ `golden.jsonl` (базовый скрипт это игнорирует); CG/CT —
  conformance на сохранённых / свежих сценариях; RD, SF, HS, LT, CF, CC — `random_docs`, `snapshot_fixtures`,
  `host_sim`, `loadtest`, `client_faults`, `client_contract`; PE — новый `protocol_edges.py`.

```
мутант                      вид     BUILD GC CG CT RD SF HS LT CF CC | PE | старый  новый
accepts-any-token           общий   0     1  1  0  0  0  1  0  0  0  | 1  | убит    убит
engine-token-from-snapshot  движок  0     0  1  1  1  0  1  1  0  0  | 1  | убит    убит
seq-bump-on-ignored         ядро    1     0  0  1  1  1  0  1  0  0  | 0  | убит    убит
seq-no-bump                 общий   1     0  1  1  1  1  1  1  0  0  | 1  | убит    убит
token-old-seq               общий   1     0  1  1  1  0  0  1  0  0  | 1  | убит    убит
never-need                  движок  0     0  1  1  1  0  1  1  0  1  | 1  | убит    убит
need-only-if-no-guards      движок  0     0  0  0  0  0  0  0  0  0  | 1  | ВЫЖИЛ   убит
drop-recovered-stepReply    движок  1     0  1  0  0  0  0  0  0  0  | 1  | убит    убит
drop-recovered-result       движок  0     0  1  0  0  0  0  0  0  0  | 1  | убит*   убит
overflow-ge                 движок  1     0  0  0  0  1  0  0  0  0  | 0  | убит    убит
overflow-plus1              движок  1     0  0  0  0  1  0  0  0  0  | 0  | убит    убит
restart-overflow-plus1      движок  0     0  0  0  0  0  0  0  0  0  | 1  | ВЫЖИЛ   убит
restart-keeps-resume        движок  0     0  0  0  0  0  0  0  0  0  | 1  | ВЫЖИЛ   убит
swap-exit-entry             ядро    1     0  1  1  1  0  0  1  0  0  | 1  | убит    убит
recover-no-cancel           ядро    1     0  1  0  0  0  0  0  0  0  | 1  | убит    убит
hello-version-2             движок  0     0  0  0  0  0  0  0  0  0  | 1  | ВЫЖИЛ   убит
snapshot-unknown-keys       движок  0     0  0  0  0  1  0  0  0  0  | 0  | убит    убит
snapshot-any-version        движок  0     0  0  0  0  1  0  0  0  0  | 0  | убит    убит
guards-lenient              движок  0     0  0  0  0  0  0  0  0  0  | 1  | ВЫЖИЛ   убит
timer-token-default-0       движок  0     0  0  0  0  0  0  0  0  0  | 1  | ВЫЖИЛ   убит
```
* Мутанты, выжившие в базовом шлюзе вместе с доказательствами:
  * частичный набор условий молча считается `false`;
  * `restart` пропускает переполнение `seq` и не очищает места;
  * `hello` сообщает версию 2;
  * не-булево условие молча считается `false`;
  * событие таймера без `token` принимается с жетоном 0.

  Операцию `restart` (FORMAT.md §7, HOST.md §4) не посылает ни один тест.
* Доказательства убивают 8 мутантов (`Correct.lean`, `EngineProofs.lean`), но не ловят мутанты в общем коде:
  `accepts-any-token` меняет `Snapshot.accepts`, через который идут и `Chart.step`, и `Machine.step`, —
  `compile_correct` остаётся верным. Свежие сценарии его тоже не видят (CT, RD, LT = 0): эталон собран из тех же
  `realize`/`accepts`/`encSnapshot`. Ловят только сохранённый golden и `host_sim`.
* `*` `drop-recovered-result` ловит только сохранённый golden. Вместе с H01 мутант проходит: сценарий
  `engine-bug-golden-edited` из H01 заканчивается «ВСЁ В ПОРЯДКЕ».
* fix (`53a4ffa`): `protocol_edges.py` — 11 граничных случаев. Ожидания выписаны по FORMAT.md и README, а не сняты с
  движка. Тест включён в оба шлюза, убивает все 6 выживших, на настоящем движке даёт 0 расхождений
  (`LOGS/protocol_edges-bin.log`). Не сделано: протокольные мутанты в самом `Mutate.lean` — это предложение.

### H12 — происхождение `VERIFY.log` (DOC, P3, Risk 3, CONFIRMED)
* По mtime в архиве `VERIFY.log` записан в 16:33:34. После него менялись README и AUDIT (16:46), `go-example/{init_helper,race_test}.go`
  (17:31), `main.go` (17:31:56), REVIEW.md и `verify.sh` (17:32:14). В журнале нет строки «общий словарь запроса из
  8 горутин: гонок нет», хотя REVIEW пишет «входит в verify.sh». В журнале нет коммита и версий, а `.git` архива без
  объектов, так что связать журнал с исходниками нельзя (`REPRODUCTIONS/r08_verifylog_provenance.py`, `LOGS/r08_verifylog_provenance.log`).
  Текущий скрипт, включая тест гонки, подтверждён CI-прогоном 39330382243.
* fix: `verify.sh` печатает коммит, число изменённых файлов, sha256 скрипта и версии инструментов (`4236416`);
  CI сохраняет полный вывод артефактом (`53a4ffa`); пометка в AUDIT (`565a07c`).

### H13 — числа в README/AUDIT/REVIEW не подкреплены или противоречат друг другу (DOC, P3, Risk 3, CONFIRMED)
* Ни один скрипт не выводит: ~90 000 шагов/с и ~11 мкс; «400 вариантов (было 10 100); 1000 сообщений — 1 с».
* «1000 горутин» — на деле 600 + 60 (в CI ещё 10 000 и 100 000).
* «120 000 случаев» — `verify.sh` прогоняет 30 000; про «4 зерна» журнала нет; зёрна в скриптах фиксированы.
* «~23 000 шагов/с» — в VERIFY.log 18 027, в CI 10 552, local 5 527–10 503.
* Клиент: 6 видов сбоев в README и REVIEW N01 против 13 в AUDIT и в коде. Обвязка: 4 вида против 11 (+35 проверок).
* `--quick`: «~10 с» в AUDIT и REVIEW, «~30 с» в скрипте (замер local — 18 с).
* «`null` в ожиданиях» из AUDIT §2 в `verify.sh` не входит.
* Подкреплено моими прогонами: 62 запроса golden, 30 пользователей / 140 сбоев (bin CI-verified).
* fix: `565a07c` (README, AUDIT).

### H14 — гигиена CI (TEST/BUILD, P3, Risk 2, CONFIRMED по журналам GitHub)
* `actions/checkout@v7` существует (`v7` = `3d3c42e5…`, `LOGS/ci-checkout-tags.log`) — гипотеза опровергнута.
  Lean Action CI зелёный, но выполняет только `lake build`: test и lint пропущены, `leanchecker` и axiom-audit выключены.
* `pip install jsonschema` ошибки PEP 668 не дал: пакет уже стоит (4.10.3 в `/usr/lib/python3/dist-packages`, локально 4.26).
  Гипотеза опровергнута, но шаг работает только благодаря предустановленному пакету.
* Go 1.23 → 1.23.12, совпадает с `go 1.23` в go.mod. Предупреждение кеша setup-go (нет `go.sum`).
  Предупреждение об устаревании Node 20 у `checkout@v4` и `setup-go@v5`.
* Теги не закреплены; `elan-init.sh` берётся из master через `curl | sh`; артефакта с журналом нет; оболочка по
  умолчанию — `bash -e` без `pipefail`.
* fix (`53a4ffa`, в CI НЕ запускался): `permissions: contents: read`, `shell: bash` (`-eo pipefail`), SHA-закрепление
  `checkout`/`setup-go`/`upload-artifact`, `jsonschema` ставится только при отсутствии, журнал сохраняется артефактом.
  `elan` из master оставлен — это рекомендация.

### H15 — скрытая диагностика C и `ffi/build.sh` (TEST, P3, Risk 1, CONFIRMED)
* `gcc … 2>/dev/null` и `./ffi/build.sh >/dev/null`: при отказе скрипт молча завершается кодом 1. Ложного зелёного
  нет, но и причина не видна. На gcc-тулчейне исходный `ffi/build.sh` падает (нет `libc++.a`) — это BUILD-находка
  оркестратора (`/home/user/figram-audit/LOGS/34-…`), я её не дублирую.
* fix (`4236416`): `ccstep` и журналы в `verify-logs/`.

### H16 — Go-тест памяти при перезагрузке нестабилен, ложно красный (EXAMPLE/TEST, P3, Risk 2, CONFIRMED; пересечение с ffi-go)
* Рост памяти с `Close` двухмодальный: около 0 или ~2,0–2,6 МБ. При пороге 2048 КБ — 5 отказов из 12
  (`LOGS/r14_go_reload_flaky-local.log`). Усиленный `verify.sh` дважды упал на неизменённом дереве
  (`LOGS/faults-full-hardened-final-try{1,2}-goflake/`).
* fix (`1cd4a74`): порог 4096 КБ — всё ещё в 12 раз ниже контроля без `Close` (~48 МБ), так что пропущенный `Close`
  останется красным. retest: 0 отказов из 20 (`LOGS/r14_go_reload_flaky-fixed.log`).

### Опровергнутые гипотезы (проверены)
* Кешированная сборка прячет предупреждения — нет: Lake выводит «⚠ Replayed» и само предупреждение (`LOGS/r11_lake_warning_replay.log`).
* Код 0 при нуле случаев или неполном входе — нет: `conformance`, `random_docs`, `json_fuzz`, `loadtest` дают код 1 (`LOGS/r13_zero_cases-bin.log`).
* Цепочка `( cd && go build && … )` на `verify.sh:160` безопасна: подоболочка завершает скрипт (`LOGS/r01b_constructs.log`).
* В CI хеши `bin/` совпали, сборка воспроизводима на официальном тулчейне.

## 4. Итог проверки шлюза (`verify-faults.sh`)

| неисправность | старый шлюз (код) | новый шлюз (код) | журналы |
|---|---|---|---|
| none | prebuilt 0 / full 0 (несмотря на расхождение хешей) | prebuilt 0 / full 3 (одно объявленное отступление: эталон хешей local) | `faults-prebuilt-*/`, `old-verify-sh-local-baseline.log`, `faults-full-hardened-final/` |
| golden-truncated | 0 | 1 | prebuilt-baseline-1 / hardened-final, full-hardened-1 |
| selftest-, fixtures-, random-docs-, json-fuzz-, mutants-shrunk | 0 | 1 | prebuilt-baseline-1..3 / hardened-final |
| bin-swapped | 1 (prebuilt, `sha256sum -c`) / full: проверки нет (H02) | 1 | full-hardened-1 |
| sums-tampered | full: 0 (H01) | 1 | full-hardened-1 |
| custom-axiom, native-decide, theorem-weakened | 0 | 1 | full-baseline-1,2 / full-hardened-1 |
| go-missing, race-test-not-run | 0 | 1 | full-baseline-2,3 / full-hardened-2 |
| coverage-drop-regen | 1 (косвенно, через мутации) | 1 (покрытие) | full-baseline-3 / full-hardened-3 |
| engine-bug-golden-edited | 0 | 1 | full-baseline-4 / full-hardened-4 |

Старый шлюз запускался на дереве базовой версии; для `prebuilt` и `full` в нём исправлен только `state()` в
`client_contract.py` (H10), чтобы исключить посторонние отказы.

Коды выхода нового шлюза: 0 — всё прошло; 1 — провал; 3 — пройдено с явно объявленными отступлениями
(`--quick`, `FIGRAM_WAIVE=go`, `FIGRAM_SHA256SUMS`). CI обязан считать код 3 провалом.

Запуск:
* `./verify-faults.sh prebuilt` — без Lean, ~15 мин;
* `./verify-faults.sh full` — нужен Lean; при другом тулчейне `FIGRAM_SHA256SUMS=… EXPECT_BASE=3`.

## 5. Коммиты (`audit/harness-ci`)
* `db72de1` — `client_contract`, `harness_selftest`, `json_fuzz`, `random_docs`;
* `37d0b6f` — `verify-prebuilt.sh` и `verify-faults.sh`;
* `53a4ffa` — `protocol_edges.py` и `verify.yml`;
* `4236416` — `verify.sh`, `ci/theorems.lean`, `ci/THEOREMS.expected`;
* `565a07c` — README и AUDIT;
* `b98062f` — новые неисправности в `verify-faults.sh`;
* `1cd4a74` — порог Go-теста перезагрузки.

## 6. What I could not check
* Изменённый `verify.yml` и путь «код 0» нового `verify.sh` на официальном тулчейне: нужен push, он запрещён. NOT RUN.
* C и Go локально шли только через оверлей (libstdc++ вместо libc++) — это не официальная сборка.
* Протокольные мутанты не встроены в `Mutate.lean`; порчу на уровне `protocol_edges.py` самопроверка не проверяет.
  Его чувствительность подтверждена только матрицей мутантов.
* `loadtest.py` не сравнивает `recovered` — не исправлял.
* Замеры производительности из README (90 000 шагов/с, 1000 сообщений за 1 с) не воспроизводил. `leanchecker` не запускал.
* Мутанты разбора формата и компилятора (`Format`, `FormatIO`, `Compile`) — зона других агентов.
* Охват: только перечисленные скрипты и 20 мутантов. Остаточный риск — классы ошибок, которых нет ни в мутантах,
  ни в `protocol_edges.py` (например, порядок ключей в ответах, большие снимки).

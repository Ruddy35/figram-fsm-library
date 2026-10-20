# Сборка, воспроизводимость и доказательства

Базовая ревизия аудита: `audit/baseline` = `d69bbaf7881a0ae1c11b75a35cf5193ad604b925` —
побайтная копия присланного архива `figram-lean_4.zip`
(SHA-256 `7769c0030bb2c516060015039263764d778ead91a92d8b2213f4fa1d9f8d1329`). Вложенный `.git` архива был
пустым (ни одного коммита), поэтому в репозиторий импортировано содержимое без него
(`diff -r` с распакованным архивом — пусто).

Все пути `LOGS/…` ниже — относительно `audit-final/`. Каждый лог `*.log` содержит команду, каталог,
время начала и конца, код выхода, хвост stdout и stderr; рядом лежат полные `*.stdout` / `*.stderr`.

## 1. Окружение

| что | значение | лог |
|---|---|---|
| Машина | KVM, 4 vCPU Intel Xeon 2.8 GHz (AVX-512), 16 ГБ, Ubuntu 24.04.5, Linux 6.18 | `LOGS/orchestrator/00-baseline-env.txt` |
| Компиляторы | gcc 13.3, clang 18.1.3, cmake, ninja | там же |
| Go / Python | go 1.24.7 (CI: 1.23.12); Python 3.13.16, jsonschema 4.26 | там же |
| Хеши исходников baseline | `sha256sum` всех 63 файлов | там же |

## 2. Тулчейн Lean 4.35.0 — два независимых пути

### 2.1 Официальный тулчейн (удалённый runner, GitHub Actions)

Загрузка релизов с github.com из среды аудита запрещена политикой прокси (HTTP 403;
`releases.lean-lang.org` перенаправляет туда же). Поэтому базовая ревизия была запушена в
назначенную ветку `claude/kind-ramanujan-n4q8mg` репозитория `Ruddy35/figram-fsm-library`, и сработали
собственные workflow проекта:

| run | workflow | итог | что показал |
|---|---|---|---|
| [39330382243](https://github.com/Ruddy35/figram-fsm-library/actions/runs/39330382243) | `verify.yml` (ubuntu-latest, 4 ядра, elan + `lean-toolchain` → официальный 4.35.0) | success | `verify.sh` целиком: сборка 42 задания; **«ok: хеши совпадают побайтно»** — `bin/figram`, `bin/figram-engine` архива побайтно равны стрипнутой свежей сборке официальным тулчейном; 14 теорем — только стандартные аксиомы; все тесты; Go на 4 ядрах: 4,1 млн запросов из 100 000 горутин, 0 расхождений, `-race` без гонок |
| [39330382251](https://github.com/Ruddy35/figram-fsm-library/actions/runs/39330382251) | `lean_action_ci.yml` | success (30 с) | `lake build` через `leanprover/lean-action@v1` |

Выдержка лога: `LOGS/orchestrator/10-ci-verify-run-39330382243.log`.
Следствие: результаты на `bin/*` архива равносильны результатам на свежей официальной сборке
baseline; агенты помечали их «bin (CI-verified)».

### 2.2 Локальный тулчейн из исходников

| шаг | команда | код | лог |
|---|---|---|---|
| исходники | `git clone --depth 1 --branch v4.35.0 https://github.com/leanprover/lean4` → `eb6cafb7290e8bd5f2937522d8d804f58f02bafc` | 0 | — |
| сборка | `cmake --preset release && make -C build/release -j4` (≈1 ч 45 мин, stage0 → stage1) | 0 | `LOGS/orchestrator/29-lean-toolchain-build.log` |
| версия | `lean --version` → `Lean (version 4.35.0, commit eb6cafb7…, Release)`; `lake 5.0.0-src+eb6cafb` | 0 | `LOGS/orchestrator/30-*` |

Отличие от официального релиза: C/C++ собирается системным gcc 13 с libstdc++ (релиз — clang + libc++,
статически), бинарники динамически зависят от libstdc++, libgmp, libuv (`LOGS/orchestrator/33-ldd-fresh.log`).
Поэтому хеши свежей локальной сборки **ожидаемо** не совпадают с `bin/` (`LOGS/orchestrator/33-hash-vs-bin.log`,
код 1) — воспроизводимость проверена официальным путём (2.1), а не этим.

## 3. Сборка проекта из исходников (локальный тулчейн)

| шаг | команда | код | лог | итог |
|---|---|---|---|---|
| Lean-библиотека и исполняемые | `lake build` (рабочее дерево `audit/ref`) | 0 | `LOGS/orchestrator/31-lake-build.log` | 42 задания, 20 с, **0 предупреждений** |
| Статическая библиотека API | `lake build Figram FigramApi:static` | 0 | `LOGS/orchestrator/32-*` | — |
| Исходный `ffi/build.sh` | `bash ffi/build.sh` | **1** | `LOGS/orchestrator/34-ffi-build-original.log` | падает на `cp …/lib/libc++.a, libc++abi.a, libuv.a, libgmp.a`: скрипт рассчитан только на раскладку официального релиза (находка BUILD FFI-02) |
| Адаптированный пакет `libfigram.a` | `libfigram.a` из `ffi/build.sh` + `libInit.a`, `libleanrt.a` + системные `libuv.a`, `libgmp.a` + пустые заглушки `libc++.a`, `libc++abi.a`; линковка с `-lstdc++` | 0 | `LOGS/orchestrator/fresh-README.txt` (состав и флаги линковки) | помечено TEST-ONLY |
| C-обвязка | обычная сборка и ASan+UBSan против свежего `libfigram.a` | 0 / 0 | `LOGS/orchestrator/35-c-harness-{plain,asan}.log` | 16 потоков × 20 000 вызовов — 0 расхождений; 5000 загрузок/выгрузок — рост RSS −14 МБ против +49,8 МБ у контроля без выгрузки |
| Go (cgo, настоящий `libfigram.a`) | `go build`, `go build -race`, `go test -race` | 0 / 0 / 0 | `LOGS/orchestrator/36-go-*.log` | 24 600 запросов, 0 расхождений (обычная и race-сборка) |
| `verify.sh` без изменений | `./verify.sh` | **1** | `LOGS/orchestrator/37-verify-sh-original-fresh.log` | доходит до шага C и падает на `ffi/build.sh` (см. выше). **На шаге хешей печатает расхождение и продолжает работу** |
| `verify.sh` с минимальной адаптацией | ветка `audit/ref-adapted` (коммит d8a548b, TEST-ONLY: `ffi/build.sh` берёт системные архивы, в `verify.sh` +`-lstdc++`), `CGO_LDFLAGS=-lstdc++ ./verify.sh` | **0** | `LOGS/orchestrator/38-verify-sh-adapted-fresh.log` | все шаги зелёные, итог «ВСЁ В ПОРЯДКЕ» — **при этом шаг хешей напечатал расхождение `bin/`**: ложнозелёный итог подтверждён на реальном прогоне (находка TEST T-01) |

## 4. Доказательства

| проверка | кто | результат | лог |
|---|---|---|---|
| Статический аудит | proofs | нет `sorry`, `admit`, `axiom`, `native_decide`, `unsafe`, `extern`, `implemented_by`, `csimp`, `macro`/`elab`, `run_elab`, `set_option`; 14 `partial def`, 23 `#guard` (это тесты) | `agents/proofs/LOGS/00-*` |
| Сборка с нуля | proofs | `rm -rf .lake; lake build` — 42 задания, 0 предупреждений | `agents/proofs/LOGS/02-*` |
| `#print axioms` по **всем 79** теоремам исходников (список извлекается программно) | proofs | только `propext`, `Quot.sound`, `Classical.choice` (26/35/17/1 по наборам) | `agents/proofs/LOGS/03-*` |
| То же по окружению: 599 теорем, 925 определений, 15 opaque модулей `Figram.*` | proofs | PASS, объявлений `axiom` нет | `agents/proofs/LOGS/04-*` |
| Повторная проверка ядром | proofs | `leanchecker Figram` и `leanchecker --fresh Figram` (с Init) — 0 | `agents/proofs/LOGS/05-*`, `06-*` |
| Формулировки = описанным | proofs | `Statements.lean` закрепляет 20 теорем README/AUDIT; отрицательный контроль — 2 ошибки | `agents/proofs/LOGS/07-*`, `08-*` |
| 14 строк VERIFY.log | proofs | побайтно совпадают с локальным выводом | — |
| Официальный тулчейн | CI | те же 14 теорем — стандартные аксиомы | `LOGS/orchestrator/10-ci-*` |
| Ветка `audit/integration` после всех исправлений | integration | 601 теорема окружения, аксиомы = baseline (+ автолемма `staleCancels.eq_1` без аксиом); строгий шлюз и leanchecker проходят; формулировки 79 теорем не менялись | `agents/integration/LOGS/52-*`, `53-*`, `54-*` |

### 4.1 Что именно доказано (и чего нет)

Доказательства корректны и проверены ядром, но относятся к **моделям**, а не ко всему исполняемому пути:

* `compile_correct` / `run_correct` / `session_correct`: скомпилированный автомат = эталонная семантика
  `Chart.step` **при `c.compile.wf`**. `Chart.step` — определение, а не теорема: соответствие прозе README
  (решения 1–8) доказательствами не покрыто. Обе стороны используют общие `realize`, `Snapshot.accepts`,
  `exit/entry` — поэтому дефект CC-01 (таймер старой версии документа) был невидим для доказательства.
  Независимый оракул на Python (агент core-compiler) показал 0 расхождений с движком в пределах одной
  версии документа на ≈465 тыс. шагов и нашёл CC-01 между версиями.
* `decode_encode`, `encode_injective`, `check_roundtrip` — на модели JSON `J`, при `DocWF`. Текстовый слой
  (`FormatIO`, все функции `partial`) не доказан; на нём найден контрпример FC-01 / F-PROOFS-02:
  документ, прошедший `check`, после `fmt` не читается.
* `decSnapshot_encSnapshot`, `step_snapOK`, `reply_readable` — модель; `SnapOK` не требует уникальности
  ключей (F-PROOFS-07); «движок никогда не выдаёт…» доказано только для `step`.
* `handle_step` — о `handleObj` на уже разобранном объекте, не о строковом `handle`.
* Решение 7 (жетон таймера) не является свойством модели ядра: `.named "timer:t"` срабатывает без жетона;
  обеспечивает его только `Engine.decEvent` (F-PROOFS-04, доказано леммами в `FigramAudit.Glue`).
* Гипотеза `d.check = []` формально зависит от непрозрачных `partial`-функций (F-PROOFS-06).

Агент proofs доказал недостающие связующие леммы (ветка `audit/proofs`, `FigramAudit/Glue.lean`, стандартные
аксиомы, leanchecker): `check = [] → compile.wf`; `Engine.load = .ok m → wf ∧ namesOK`; `decSnapshot` даёт
только `SnapOK`; `start`/`restart` дают `SnapOK`; шаг загруженного движка = эталонный `Chart.step`.

### 4.2 Доверенная база (не проверялась)

Ядро Lean (leanchecker — то же ядро; независимые lean4lean/nanoda недоступны офлайн), компилятор Lean → C,
C-компилятор, рантайм Lean (mimalloc, libuv), glibc, рантаймы Go и Python, код условий и действий разработчика.

## 5. Воспроизводимость `bin/` и процесс выпуска

* Baseline: `bin/*` = официальная свежая сборка (CI, 2.1). Подтверждено.
* После исправлений Lean-кода в `audit/integration` бинарники `bin/` **больше не соответствуют исходникам**
  (INT-01): `verify-prebuilt.sh` на integration красный (json_fuzz: старый `bin/figram` содержит FC-01).
* Шаг хешей в `verify.sh` нефатален (T-01), поэтому расхождение не остановит выпуск. Нужно:
  1. сделать шаг фатальным (исправление есть в ветке `audit/harness-ci`, см. ISSUES_TESTS.md);
  2. пересобрать `bin/` официальным Lean 4.35.0 через elan/CI, `strip`, обновить `bin/SHA256SUMS` и `VERIFY.log`
     отдельным коммитом выпуска;
  3. не переносить в выпуск TEST-ONLY коммиты адаптации тулчейна (`0efd043`, `28a3439` в integration);
  4. убедиться, что `verify.sh` и `verify-prebuilt.sh` зелёные на официальном тулчейне.

## 6. Как повторить

```bash
# официальный путь (нужен доступ к GitHub releases): как в .github/workflows/verify.yml
./verify.sh

# локальный путь из исходников Lean (как в аудите)
git clone --depth 1 --branch v4.35.0 https://github.com/leanprover/lean4 && cd lean4
cmake --preset release && make -C build/release -j"$(nproc)"
export PATH=$PWD/build/release/stage1/bin:$PATH
cd <figram> && lake build
# verify.sh на таком тулчейне — только с TEST-ONLY адаптацией (ветка audit/ref-adapted, PATCHES/test-only/)
CGO_LDFLAGS=-lstdc++ ./verify.sh
```

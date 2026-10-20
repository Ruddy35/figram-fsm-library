# Аудит доказательств Figram (агент `proofs`)

Базовая ревизия: `audit/baseline` = d69bbaf. Инструменты: Lean 4.35.0 (eb6cafb7), собран из исходников (gcc); `source /home/user/figram-audit/env.sh`. Рабочее дерево: `/home/user/figram-audit-worktrees/proofs`, ветка `audit/proofs`. Журналы: `/home/user/figram-audit/agents/proofs/LOGS/`, воспроизведения: `/home/user/figram-audit/agents/proofs/REPRODUCTIONS/`.

## 1. Область и контракт

Что проверено:
- все теоремы в `Figram/*.lean`: CoreProofs, Compile, Correct, Run, FormatProofs, EngineProofs — 79 штук по исходникам;
- все утверждения о них в README.md, AUDIT.md, FORMAT.md, REVIEW.md, VERIFY.log;
- шаг проверки доказательств в `verify.sh`.

Что сделано:
- (1) статический аудит;
- (2) настоящая сборка, `#print axioms` для всех теорем (список извлекается из исходников программно), проверка по окружению и повторная проверка ядром (`leanchecker`);
- (3) сверка точных формулировок с текстом документов;
- (4) контрпримеры при снятии условий, доказательство недостающих связующих лемм;
- (5) проверка чувствительности шлюза аксиом и замена его строгим шлюзом.

Не проверено (вне моей области или невозможно):
- правильность эталонной семантики `Chart.step` относительно решений 1–8;
- текстовый слой JSON формально: все функции `partial`, для логики они непрозрачны;
- C/Go/Python.

## 2. Таблица гарантий

| гарантия | тест | оракул | прогнано? | покрытие | пробел |
|---|---|---|---|---|---|
| Сборка без предупреждений | `rm -rf .lake; lake build` | 0 строк warning | да, LOGS/02 (exit 0) | 42 задания, все модули пересобраны | — |
| Все теоремы используют только propext/Quot.sound/Classical.choice | `audit/proofs/gen_axioms.py` → `AllAxioms.lean` | разбор вывода, число ответов = числу теорем | да, LOGS/03 | 79 из 79 теорем исходников (26 / 35 / 17 / 1 по наборам аксиом) | `#print axioms` берёт данные импортированных модулей из .olean — закрыто leanchecker |
| То же по окружению | `audit/proofs/AxiomGate.lean` | `collectAxioms` ⊆ разрешённых; объявлений `axiom` нет; список исходников ⊆ окружения | да, LOGS/04 (PASS) | 599 теорем, 925 определений, 15 opaque модулей `Figram.*`; лишние 147 — автолеммы (eq_N, inj, sizeOf) | не ловит непроверенное ядром объявление (канарейка B) |
| Повторная проверка ядром | `lake env leanchecker Figram`, `--fresh Figram`, `leanchecker FigramAudit` | exit 0 | да, LOGS/05, 06, 14, 28 | 17 модулей, при `--fresh` вместе с Init | то же ядро Lean; независимый проверяющий (lean4lean) не запускался |
| VERIFY.log соответствует сборке | сравнение 14 строк | `diff` | да | 14 строк побайтно совпали | — |
| Формулировки = описанным | `Statements.lean`: `example : <формулировка> := @thm` | компиляция | да, LOGS/07; отрицательный контроль LOGS/08 (2 ошибки, exit 1) | 20 теорем README/AUDIT | смысл прозы сверен вручную (раздел 3) |
| Условия теорем необходимы | `Counterexamples.lean` CE1, CE3, CE7 | `decide`/`rfl` в ядре | да, LOGS/09, 23; отрицательный контроль LOGS/10 (4 ошибки) | `wf`, `DocWF` | — |
| Решение 7 (жетон таймера) | CE2 (модель), `timer_named_event.sh` (движок), лемма G6 | `decide`; ответы fresh/figram-engine | да, LOGS/09, 30 | модель и протокол | в проекте теоремы нет — добавлена G6 |
| Текстовый круг документа, прошедшего `check` | `TextRoundTrip.lean`, `fmt_exponent.sh` | `parse(print(encode d))` даёт ту же модель | да: до — LOGS/17, 18 (5 из 15 провал); после — LOGS/20, 21 | 15 числовых случаев | формально не доказуемо (partial) |
| Чувствительность шлюза | `REPRODUCTIONS/axiom_gate_canary.sh` | ожидаемые red/green | да, LOGS/16-canary-baseline-verify, LOGS/16-canary-patched-verify (все ожидания выполнены) | 3 класса: axiom, native_decide, непроверенное объявление | — |
| Связующие леммы G1–G10 | `lake build FigramAudit`, GlueAxioms, leanchecker | exit 0, стандартные аксиомы | да, LOGS/27–29 | 21 теорема | унаследованная оговорка про opaque (F-PROOFS-06) |

## 3. Сверка утверждений с точными формулировками

| Теорема | Точная формулировка | Документ | Вердикт |
|---|---|---|---|
| `Chart.compile_correct` | `c.compile.wf = true → ∀ s ev env, c.compile.step s ev env = c.step s ev env` | README:52, условие на :69 | ТОЧНО (с `wf`; без него ложно — CE1). Сравнение идёт с эталоном `Chart.step`, а это определение: решения 1–8 теоремами не доказаны. |
| `run_correct`, `session_correct` | то же для `run`, со старта | README:53 | ТОЧНО |
| `start_correct` | `c.compile.start = c.start` | — | ТОЧНО, но `rfl` |
| `chart_run_valid` | `wf → (c.run s (x::rest)).1.state ∈ c.leafIds` | README:54 | ТОЧНО |
| `Machine.step_state_valid` | `m.wf → (m.step s ev env).1.state ∈ m.states` | README:55, REVIEW F06 | ТОЧНО (условие только на автомат) |
| `step_guard_local` | совпадение на `requiredGuards` → шаги равны | README:56 | ТОЧНО |
| `decode_encode` | `DocWF d → decode (encode d) = .ok d`, модель `J` | README:57 | ЗАВЫШЕНО: нет `DocWF` (CE3) и речь о модели, не о тексте |
| `encode_injective` | `DocWF d₁ → DocWF d₂ → encode d₁ = encode d₂ → d₁ = d₂` | README:58 | ЗАВЫШЕНО: без `DocWF` ложно (CE3); следствие `decode_encode` |
| `check_roundtrip` | `d.check = [] → decode (encode d) = .ok d`, модель `J` | README:59, FORMAT:91–95 | Для модели ТОЧНО, для текста ЛОЖНО (F-PROOFS-02); используется только `wfOK` |
| `decode_requires` | `decode j = .ok d → ∀ f ∈ d.requires, f ∈ knownFeatures` | README:60 | ТОЧНО |
| `toChart_strip` / `toChart_decls` | `d.strip.toChart = d.toChart` / поля описаний не читаются (`rfl`) | README:61 | ТОЧНО для поведения после загрузки; аннотации влияют на приём документа (`check`, `parse`) |
| `decSnapshot_encSnapshot` | `SnapOK s → decSnapshot (encSnapshot s) = .ok s`, модель | README:62 | ТОЧНО для модели; ключи не обязаны быть уникальными (CE5) |
| `step_snapOK`, `reply_readable` | `wf → namesOK → SnapOK s → seq' ≤ maxInt → …` | README:63 «никогда не выдаёт» | ЗАВЫШЕНО: только `step`, нужен `SnapOK` входа без леммы о `decSnapshot`, только модель → G8–G10 |
| `handle_step` | о `handleObj` на разобранном `Obj` при 7 гипотезах | README:64 | ТОЧНО для `handleObj`; строковый `handle` не покрыт; связи с `Chart.step` нет → G7 |
| README:69 «`wf` выполняет `check`» | теоремы не было | | ВЕРНО по коду, теперь доказано (G1–G3) |
| AUDIT:45–47 «условия выполняются для любого исполняемого автомата» | теоремы не было | | ВЕРНО по коду `Engine.load` → G4. Исключение — тестовый режим `figram-engine --mutate`: мутированный автомат не перепроверяется (по чтению кода) |
| README:5, AUDIT:8–9 «только стандартные аксиомы» | — | | ПОДТВЕРЖДЕНО для всех 79 теорем, но `verify.sh` это не проверял (F-PROOFS-01) |

## 4. Находки

### F-PROOFS-01 — шлюз доказательств в `verify.sh` не ловит нестандартные аксиомы

- **Модуль / класс:** verify.sh, шаг «Теоремы без sorry»; TEST.
- **Путь:** CI и ручная проверка; это гарантия доверия к доказательствам, а не путь исполнения в продакшене.
- **Условие срабатывания:** в доказательство попадает `axiom`, `native_decide` (в 4.35 создаёт вспомогательную аксиому `<thm>._native.native_decide.ax_N`) или объявление, добавленное без проверки ядром (`addDeclCore doCheck:=false`).
- **Последствие:** утверждение README/AUDIT «только стандартные аксиомы» не защищено. Шаг печатает 14 из 79 теорем и ищет только слово «sorry». Теорема `1 = 2`, полученная взломом окружения, показывает «does not depend on any axioms».
- **Вероятность:** низкая (злой умысел или ошибка), но шлюз — единственная автоматическая защита.
- **Оценка:** Risk 4, P2, CONFIRMED, базовая ревизия d69bbaf.
- **Воспроизведение:** `VERIFY_REV=d69bbaf REPRODUCTIONS/axiom_gate_canary.sh`.
- **Ожидалось / получено:** ожидалось, что шаг станет красным; на деле старый шаг проходит, ожидания канарейки выполнены, exit 0 (LOGS/16-canary-baseline-verify).
- **Исправление:** `audit/proofs/strict_gate.sh`. Он делает статический запрет sorry/admit/axiom/native_decide/skipKernelTC/run_elab/modifyEnv/addDeclCore/unsafe/csimp; `#print axioms` для всех теорем из исходников со сверкой их числа; AxiomGate по окружению; закреплённые формулировки; `leanchecker Figram`. Встроен в `verify.sh`.
- **Регрессионный тест и повтор:** канарейки с новым `verify.sh` красные, чистое дерево зелёное (LOGS/16-canary-patched-verify, LOGS/25, LOGS/26).
- **Коммиты:** e0091ba, 90201dd.

### F-PROOFS-02 — `figram fmt` пишет текст, который сам figram не читает

- **Модуль / класс:** `Figram/FormatIO.lean` (`render`); PRODUCT.
- **Путь:** `figram fmt`, `Api.fmt`/`figram_fmt`, любая печать документа; эхо `id` в движке.
- **Условие срабатывания:** число в аннотации, у которого после нормализации показатель меньше −1000. Например `1.5e-1000`: хранится как 15·10^−1001 и печатается `15e-1001`, а парсер разрешает модуль показателя не больше 1000.
- **Последствие:** документ проходит `check` и загружается, но его каноническая запись отвергается («показатель степени по модулю больше 1000»). Пересохранение через `fmt` ломает загрузку бота. Это прямой контрпример к FORMAT:91–95 на уровне текста.
- **Вероятность:** низкая: нужны экзотические числа в аннотациях.
- **Оценка:** Risk 3, P2, CONFIRMED на `/home/user/figram-audit/fresh/figram` (и на PREBUILT bin/), базовая ревизия d69bbaf.
- **Воспроизведение:** `REPRODUCTIONS/fmt_exponent.sh` (exit 0 означает, что ошибка воспроизведена).
- **Ожидалось / получено:** ожидалось, что `check` вывода `fmt` пройдёт (rc 0); на деле rc 1 (LOGS/18).
- **Исправление:** такие числа печатаются точной десятичной дробью (d116a64).
- **Регрессионный тест:** `audit/proofs/TextRoundTrip.lean`: до исправления 5 из 15 случаев провалены (LOGS/17), после — все проходят (LOGS/20).
- **Повтор:** `fmt_exponent.sh` → exit 1 (LOGS/21); golden и pizza не изменились; json_fuzz 2×30000, расхождений 0 (LOGS/22, 22b).

### F-PROOFS-03 — таблица теорем в README завышает гарантии

- **Модуль / класс:** README.md:57–64, FORMAT.md:91–95; DOC.
- **Суть:** в README:57–58 нет условия `DocWF`. CE3 строит два разных документа с одинаковым `encode` (доказано `rfl`), так что без `DocWF` `decode (encode dB) = .ok dA ≠ .ok dB`. В README:59 и FORMAT:91–95 утверждения о модели `J` поданы как гарантия для текста (см. F-PROOFS-02). README:63 «никогда не выдаёт» покрывает только `step`. README:64 — про `handleObj`, а не про строковый `handle`.
- **Оценка:** Risk 2, P3, CONFIRMED.
- **Логи:** LOGS/07, 08, 09.
- **Исправление:** d9934d5.

### F-PROOFS-04 — решение 7 не является свойством модели

- **Модуль / класс:** Core.lean (`Snapshot.accepts`), Chart.step; PRODUCT/DOC.
- **Суть:** `accepts (.named _) = true`, а `Event.key (.named "timer:t") = "timer:t"`. Именованное событие срабатывает по строке таймера без жетона и даже без активного таймера (CE2, доказано `decide` в ядре). Защищает только `Engine.decEvent`: свежий движок отвечает ошибкой «нужно поле token», устаревший жетон игнорирует, текущий срабатывает (LOGS/30).
- **Риск:** при встраивании `Machine.step` в обход протокола (новый API, где `Event.named` строится из пользовательского текста).
- **Оценка:** Risk 2, P3, CONFIRMED.
- **Исправление (доказательство):** леммы `decEvent_named` и `timer_key_needs_token` — любой сработавший шаг по событию из `decEvent` с ключом `timer:` есть `.timer id tok` с текущим жетоном (e0091ba).

### F-PROOFS-05 — недостающие связующие леммы между утверждениями и теоремами

- **Класс:** DOC/assurance; Risk 1, P3, CONFIRMED. Теперь доказано в `audit/proofs/FigramAudit/Glue.lean` (lean_lib `FigramAudit`, не входит в цели по умолчанию):
  - G1 `Chart.lint_eq` (`rfl` через дословную копию `lint` без последней проверки) и `lint_nil_wf : c.lint = [] → c.compile.wf`;
  - G2 `check_compile_wf : d.check = [] → d.toChart.compile.wf`;
  - G3 `check_compile_correct`;
  - G4 `load_sound : Engine.load txt = .ok m → ∃ d, d.check = [] ∧ m = d.toChart.compile ∧ m.wf ∧ namesOK m`;
  - G5/G6 `decEvent_named`, `timer_key_needs_token`;
  - G7 `engine_step_is_reference`: при гипотезах `handle_step` ответ движка равен `result … (d.toChart.step s ev (envOf gs))`;
  - G8 `start_snapOK`, `restart_snapOK`;
  - G9 `decSnapshot_sound : decSnapshot j = .ok s → SnapOK s`;
  - G10 `step_reply_readable` без внешней гипотезы `SnapOK`.
- **Проверка:** стандартные аксиомы, leanchecker — exit 0 (LOGS/27–29).
- **Отрицательные проверки:** CE7 (документ с висячей целью: `wf = false`, `check ≠ []`); CE8 (`decSnapshot` отвергает то, что запрещает `SnapOK`).

### F-PROOFS-06 — гипотеза `d.check = []` зависит от непрозрачных partial-функций

- **Класс:** DOC; Risk 1, P3, CONFIRMED.
- **Суть:** формулировки `check_wfOK` и `check_roundtrip` (и мои G2/G3/G4/G7) через определение `Doc.check` зависят от opaque `nodeErrors`, `allTimers`, `allEvents`, `J.keysUnique`; для G4/G7 добавляется ещё `parse` (LOGS/32, `OpaqueDeps.lean`). Логическое значение этих функций не задано. Поэтому гипотезу нельзя доказать ни для одного конкретного документа (CE6: `decide` не справляется), и прогон `check` во время работы её формально не устанавливает.
- **Почему не опасно:** доказательства используют только непрозрачно-независимые компоненты (`wfOK`, `lint`); их значения во время работы совпадают с логическими при доверии компилятору.
- **Рекомендация:** формулировать гарантии через `d.wfOK = true` (уже есть `wfOK_sound`) и `lint = []` (G1).

### F-PROOFS-07 — в `SnapOK` нет уникальности ключей

- **Класс:** DOC; Risk 1, P3. На модели CONFIRMED, недостижимость — LIKELY.
- **Суть:** снимок `⟨"a", [], [("t",1),("t",2)], 2⟩` удовлетворяет `SnapOK`, и `decSnapshot_encSnapshot` для него верна, но текст `{"at":"a","timers":{"t":1,"t":2},"seq":2}` отвергает `parse` (CE5, LOGS/09).
- **Почему, вероятно, недостижимо из движка:** `assign`/`erase` сохраняют уникальность, а парсер отвергает повторы. Теоремы об этом нет.

### Наблюдения без отдельной находки

- `figram-engine --mutate` исполняет мутированный автомат без перепроверки `wf`/`namesOK` (тестовый режим; по чтению кода).
- `toChart_decls` и `start_correct` верны по определению (`rfl`).
- Эхо `id` в движке печатает `15e-1001` (до исправления).

## 5. Коммиты (ветка `audit/proofs`)

- e0091ba — инструменты аудита и леммы;
- d116a64 — исправление FormatIO;
- 90201dd — строгий шлюз в `verify.sh`;
- d9934d5 — документация;
- 44f8b39 — CE7/CE8;
- b5bc7e9 — OpaqueDeps.

## 6. Что не удалось проверить

- Независимый проверяющий ядра (lean4lean, nanoda) — NOT RUN: недоступен без сети. Использован leanchecker, то есть то же ядро.
- Официальная сборка clang/libc++: только журнал CI оркестратора (LOGS/10-ci-…).
- Формальные доказательства текстового слоя не делались: все функции `partial`.
- Соответствие `Chart.step` решениям 1–8 и `toChart` описанию FORMAT — только тесты проекта, мной не перепроверялись.
- Полный `verify.sh` не запускался: шаг сверки хешей bin/ неприменим к gcc-сборке. Шаг доказательств запускался отдельно (LOGS/25).
- Остаточный риск: доказательства относятся к моделям `J`/`Obj`. Разбор и печать текста, `Engine.handle`, CLI и FFI покрыты только тестами и фаззингом.

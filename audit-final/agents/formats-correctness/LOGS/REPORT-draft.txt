# formats-correctness — корректность формата документа и текстового слоя JSON

Агент: formats-correctness. Базовая версия: `audit/baseline` = d69bbaf7881a0ae1c11b75a35cf5193ad604b925.
Ветка: `audit/formats-correctness`, рабочее дерево `/home/user/figram-audit-worktrees/formats-correctness`.
Бинарники: `bin/figram` (sha256 a08c666c…), `bin/figram-engine` (sha256 8d96cf27…) — bin (CI-verified = official
fresh build), см. `/home/user/figram-audit/LOGS/10-ci-verify-run-39330382243.log`; хеши — `LOGS/binaries-sha256.txt`.
Все пути LOGS/ и REPRODUCTIONS/ ниже — относительно `/home/user/figram-audit/agents/formats-correctness/`.

## 1. Scope & contract

Проверялось (только корректность; производительность и исчерпание ресурсов — у formats-security, большие входы
не генерировались):

* `Figram/FormatIO.lean` — разбор JSON (RFC 8259 + отклонения: повтор ключа, |показатель| ≤ 1000, глубина ≤ 256,
  непарные суррогаты) и печать (`render`, `renderCompact`);
* `Figram/Format.lean` — `decode`, `encode`, `Doc.check`;
* `figram.schema.json`, `snapshot.schema.json` против декодера / `check` / `decSnapshot`;
* CLI `figram check | fmt | json`, разбор запросов в `Figram/Engine.lean` (`handle`, `decEvent`, `decGuards`,
  `decSnapshot`), диагностика CLI и `figram-engine` при загрузке документа.

Контракт, против которого проверялось: FORMAT.md §2 (К1, К3, К5а/б), §3 (решения 11–12, «Числа», V1–V5,
«документ, прошедший check, гарантированно записывается и читается обратно»), §4 (грамматика), §6, §7
(«снимок строгий: движок принимает ровно то, что разрешает snapshot.schema.json»), заголовок FormatIO.lean,
AUDIT.md §4 (замена невалидного UTF-8 на U+FFFD).

Тесты (ветка, `tests/formats/`): `common.py` (независимый оракул на Python: модель чисел m·10^e, повторная
реализация печати и декодера/кодировщика по FORMAT.md §4), `a_roundtrip.py`, `b_rfc_table.py`,
`c_schema_table.py`, `d_engine_requests.py`, `e_cli_names.py`, `mutant_figram.sh`, `run_all.sh`.
Прогон всего набора: `tests/formats/run_all.sh` → `LOGS/run_all-summary.txt` (23 запуска, все с ожидаемым
кодом: обычные — 0, `--strict` по строкам находок — 1, каждый испорченный оракул/мутант — 1).

## 2. Guarantee table

| guarantee | test | oracle | actually run? | coverage | gap |
|---|---|---|---|---|---|
| G1. `fmt x` = decode∘encode∘print(x) по FORMAT §4 (К1, К3б: умолчания не пишутся, порядок неизвестных ключей сохранён) | a_roundtrip P1 | Python: json + object_pairs_hook, числа m·10^e, собственный декодер/кодировщик/печать (common.py) | да, LOGS/a-roundtrip.log, rc 0 | 253 документа (`figram random s 1+s%15`, s=0…249, + 3 examples) × 5 вариантов = 1265: ensure_ascii/суррогатные пары, перестановка известных ключей, явные умолчания (`resume:false`, `do:[]`, `on:{}`, `timers:{}`, `requires:[]`, `variables:{}`), `figram` как 1.0/1e0/10e-1/0.1e1/100E-2, задержки как 7.2e3/…0e-1/…000e-3, аннотации `x-…` и прочие неизвестные ключи со случайными значениями (юникод, \u0000, ESC, U+2028, числа с дробью/показателем ≤300, вложенность ≤3), случайные пробелы/CR/LF/TAB | генератор не даёт `variables`, юникодных имён узлов, `requires` ≠ []; размер ≤ 15 сообщений |
| G2. Идемпотентность: `fmt(fmt x)` = `fmt x` (Figram читает то, что напечатал) | a_roundtrip P2, EDGE | сам Figram | да, 1265/1265 ok; EDGE 3/3 НЕТ → **FC-01** | как G1 + 3 строки с показателем < −1000 | — |
| G3. Перечитывание печати Python-ом даёт ту же модель | a_roundtrip P3 | json.loads + object_pairs_hook | да, 1265/1265 | как G1 | — |
| G4. К5а: аннотации, умолчания, запись чисел не меняют поведение | a_roundtrip P4: `check`=ok, `stats` и `trace x 3 8 seed` побайтно равны исходному | базовый документ | да, 1265/1265 | как G1 | trace — 3 сценария × 8 шагов на вариант |
| G5. Разбор = RFC 8259 + документированные отклонения; печать точна | b_rfc_table T1/T2 | рукописная таблица 132 строк + Python json с отклонениями | да, LOGS/b-rfc.log, rc 0 | -0, 1E+2, 0.0e-0, 1.5e3, 1e±1000/1001, ведущие нули, \b\f\n\r\t\/\", é, пары/непарные суррогаты, сырые управляющие, DEL, U+2028, BOM, NBSP/VT/FF/U+3000, висячие запятые, `truex`/`true1`/`nul`, повтор ключа (в т.ч. через \u0061), глубина 256/257, пустой вход, скаляры; + 6 строк с невалидным UTF-8 | LF внутри одного случая не проверить через `figram json` (строка = случай) — покрыто в a_roundtrip V4 |
| G6. То же на случайных корректных значениях | b_rfc_table R | Python json + отклонения, печать по исправленному правилу чисел | да, 4000 случаев: 3924 совпали, 76 — только запись числа с e < −1000 (FC-01), принятие/отказ — 4000/4000 | вложенность ≤5, строки ≤5 символов из 20 «трудных», числа с дробью и показателем до 1000 | — |
| G7. Печать всегда перечитывается Figram-ом | b_rfc_table T3, R-T3; json_fuzz.py (усилен) | сам Figram | да: 76 из 4000 случайных, 2 строки таблицы, 26 из 9786 записей json_fuzz — НЕТ → **FC-01** | — | — |
| G8. Схема документа ≡ декодер+check, кроме документированного | c_schema_table | рукописные ожидания 76 строк | да, LOGS/c-schema.log, rc 0 | `figram: 1.0/1e0/10e-1/true/"1"/2`, пустые `children`, `initial` не ребёнок/внук/"", пустые имена (узел, событие, таймер, условие, `to`, `if`, `do`), `x-` и прочие ключи, `x-ui` внутри `children`, задержки 7.2e6/7200000.0/72e5/-0/-0.0/0.5/-1/2^53−1/2^53/9e18/1e19/±1e400/«строка»/bool/null, `resume:false/"true"/null/true→сообщение`, `do:[]/"k"`, `requires: []/["x"]/["x","x"]/"x"`, повтор узла в разных разделах, V3–V5, повтор ключа, суррогат, показатель, глубина | валидатор — только Python jsonschema 4.26 (ajv не запускался) |
| G9. Снимок: схема ≡ движок («ровно то, что разрешает схема») | c_schema_table (снимки) | рукописные ожидания 27 строк | да | `seq` 1.0/1e0/10e-1/-0/-0.0/1.5/2^53−1.0/…990.5/1e-400/bool/null; `v` 1/1.0/1e0/true/0/-0/null/"1"/1+1e-16; жетон 1.0; повтор `at`; суррогат; неизвестное поле | расхождения только класса prec → **FC-05** |
| G10. Разбор запросов движка | d_engine_requests | 65 точных ответов (записаны с baseline, сверены вручную с FORMAT §7) | да, LOGS/d-engine.log, rc 0 | `id` всех типов JSON, неизвестные/опечатанные поля, `op`, `guards` (bool/1/"true"/null/[]/повтор/лишние имена), `token` (5/5.0/устаревший/2^53+1/"5"/-1/1e19/нет/на обычном событии), `event` (""/"timer:"/нет/5/регистр), снимок (повтор ключа, seq 1.0/1e0/-0, v 1.0/2, null, нет), restart, два объекта в строке, CRLF, BOM | **FC-04**; id-по-значению и id:null — ET-07, жетоны — ET-12 |
| G11. Диагностика CLI/движка не содержит сырых управляющих байтов из документа | e_cli_names | свойство P (нет C0/DEL, кроме LF в конце строки; нет строки «ok» при rc≠0; одна ошибка fmt = одна строка) | да, LOGS/e-cli.log: 8 из 9 строк нарушают P → **FC-06**; контроль с безобидными именами: 0 из 9 | ESC/CSI, OSC+BEL, LF, CR, NUL, `"`; check stdout, check/fmt stderr, engine stderr | Api.check (FFI) — у ffi-go |
| G12. Lean-строка сохраняет NUL и хвост после него | e_cli_names | `fmt` печатает `a\u0000HIDDEN` | да, True | CLI | усечение в C-строках — у ffi-go |

Отрицательные проверки (каждая увидена красной, LOGS/run_all-summary.txt):
* a_roundtrip с испорченным оракулом `sort-unknown` (65 провалов), `keep-resume-false` (64), `keep-num-text` (45),
  `drop-unknown` (69); с испорченным `fmt` (`mutant_figram.sh`, `"resume": true`→false) — 135; `--strict` — rc 1 (FC-01);
* b_rfc_table `dup-last-wins` (15), `no-surrogate` (10), `float` (643); `--strict` — rc 1 (161: FC-01, FC-03),
  `--strict fc01` — rc 1 (156); `json_fuzz.py` с проверкой перечитывания Figram-ом — rc 1 (LOGS/jsonfuzz-baseline-bin.log);
* c_schema_table со схемой без `minLength/minProperties` (9), с `const: 2` (40), с `integer`→`number` (2);
* d_engine_requests `flip` (1), `lax-id` — оракул «id эхом побайтно» (7);
* e_cli_names `--strict` — rc 1; `--control` (безобидные имена) — 0 нарушений: детектор не тривиально красный.

## 3. Findings

### FC-01. Печать числа с |показателем| > 1000: `fmt` выдаёт текст, который сам Figram не читает
* module: `Figram/FormatIO.lean` `render` (`.num m e`, ветка `e < -30`); class PRODUCT (+ TEST: почему не пойман).
* reachable production path: `figram fmt`, `Api.fmt` (FFI) — любое число в аннотации/расширении; затем
  `figram check`/`figram-engine`/`figram_load` на каноничной записи.
* trigger: число с дробной частью и показателем около −1000 (`1.0e-1000` → модель 10·10^−1001) или > 1000 цифр
  после точки (`0.000…01`). Разбор ограничивает показатель в тексте, а печать пишет `e = ex − fc` без ограничения.
* impact: К1/«каноническая форма» на уровне текста нарушены: документ проходит `check` (ok), `fmt` печатает
  `"zoom": 10e-1001`, а `check`/`fmt`/движок на этом выводе — «показатель степени по модулю больше 1000», rc 1.
  Утверждение FORMAT.md §3 «документ, прошедший check, гарантированно записывается и читается обратно» верно
  только на уровне модели J (теорема `check_roundtrip`), не текста. Если конвейер переписывает файл через
  `figram fmt`, следующий запуск бота не загрузит документ (контролируемый отказ, значение в тексте сохранено).
* likelihood: низкая — JS/Python/Go никогда не печатают показатель < −324; только ручной или чужой генератор.
* Risk 2, P3, CONFIRMED (bin, CI-verified).
* repro: `bash REPRODUCTIONS/fc01_exponent_roundtrip.sh`; LOGS/repro-fc01_exponent_roundtrip.log.
* expected / actual: `check(fmt x)` = ok / «показатель степени по модулю больше 1000», exit 1.
* статистика: 76 из 4000 случайных значений (LOGS/b-rfc.log), 2/2 строки таблицы, 3/3 документа (LOGS/a-roundtrip.log).
* почему не поймано: `ffi/python-example/json_fuzz.py` проверяет «печать идемпотентна», перечитывая вывод Python-ом
  (Decimal принимает e-1001), а не Figram-ом; входы с показателем 990–1010 и дробью у него есть.
* proposed fix: в `render` писать научную запись только при `-maxExponent ≤ e`, иначе позиционно
  (`(e < -30 && -(maxExponent : Int) ≤ e)`); длина вывода ≤ длины входа + 1000. Обновить строку FORMAT.md «Числа»
  и заголовок FormatIO.lean. В json_fuzz.py перечитывать вывод самим Figram.
* regression test: `b_rfc_table.py --strict` (T3 n-fc01-a/b + R-T3), `a_roundtrip.py --strict` (EDGE).
* fix commit: 4a38b91 (`render`: научная запись только при `-maxExponent ≤ e`, иначе позиционно; FORMAT.md
  «Числа»); регрессионные тесты — 177355a (в т.ч. `json_fuzz.py` теперь перечитывает запись самим Figram).
* retest (сборка stage0 Lean 4.35.0 commit 5f4537b2 + gcc, НЕ официальный тулчейн — только sanity;
  LOGS/fix-stage0-*.log): одинаковые исходники baseline / с исправлением —
  `b_rfc_table.py --strict fc01` rc 1 → 0 (LOGS/fix-s0base-b-rfc-strict-fc01.log → fix-s0build-…),
  `a_roundtrip.py --strict` rc 1 → 0, `fc01_lean_check.lean` rc 1 (4 сбоя) → 0, `json_fuzz.py 30000` rc 1
  (26 из 9786 записей не читаются) → 0 (LOGS/jsonfuzz-s0base.log, jsonfuzz-s0build.log; на bin baseline — тоже 26,
  LOGS/jsonfuzz-baseline-bin.log). Без регрессий на исправленной сборке: a_roundtrip 253×5, c_schema 103, d_engine 65,
  `figram golden` побайтно = examples/golden.jsonl, random_docs.py 100 документов / 97 282 запроса — 0 расхождений.
  На официальном тулчейне — BLOCKED (TOOLCHAIN_READY нет); bin/ не пересобран, шаг verify.sh «хеши bin/» после
  исправления упадёт до пересборки.

### FC-02. Знак отрицательного нуля теряется: `-0.0` → `0.0`
* module: `FormatIO.parseNum` (модель `Int`: −0 = 0); class PRODUCT/DOC. Путь: `fmt`, аннотации.
* trigger: `-0.0`, `-0e5`, `[-0.00]` → `0.0`, `0e5`, `[0.00]`. Python `json.loads("-0.0")` = −0.0 (copysign −1),
  JS `Object.is(JSON.parse("-0.0"), -0)` = true.
* impact: аннотация редактора (координата, масштаб) с −0 после `fmt` становится +0; К1 «ничего не теряется»
  формально про модель, где −0 не выражается. Поведение бота не меняется (поля формата — неотрицательные целые).
* likelihood: низкая. Risk 1, P3, CONFIRMED (bin). repro: `REPRODUCTIONS/fc02_negative_zero.sh`,
  LOGS/repro-fc02_negative_zero.log; таблица b: строки n-neg-zero-frac, n-neg-zero-arr (класс fc02).
* expected / actual: `-0.0` / `0.0`, exit 0.
* proposed fix: задокументировать в FORMAT.md §3 «Числа» (−0 записывается как 0) либо хранить признак знака нуля.
* regression test: b_rfc_table (строки fc02 фиксируют текущее поведение). fix commit: нет.

### FC-03. Невалидный UTF-8 в потоке молча заменяется на U+FFFD; файлы — отвергаются
* module: рантайм Lean `IO.FS.Stream.getLine` в `EngineMain.loop` и `figram json`; `IO.FS.readFile` в `readDoc`/
  `loadOrFail`; class PRODUCT/DOC.
* reachable production path: запрос `figram-engine` по stdin (имя события, guards, id, снимок).
* trigger: байты `\xff`, `\xfe`, CESU-суррогат `\xed\xa0\x80`, overlong `\xc0\xaf`, `\xf4\x90\x80\x80`, обрыв `\xe2\x82`.
* impact: RFC 8259 §8.1 (текст ДОЛЖЕН быть UTF-8) и Python отвергают; Figram принимает, разные байтовые строки
  совпадают: события `"\xff"` и `"\xfe"` оба срабатывают как обработчик события `"�"`; `{"\xff":1,"\xfe":2}` —
  «повторяющийся ключ». Тот же документ из файла — «non UTF-8 data», rc 1. AUDIT.md §4 описывает замену только
  для C/Go, FORMAT.md — нет. Хосты на Python (ensure_ascii) и Go (json.Marshal сам заменяет на U+FFFD) такой
  поток не шлют.
* likelihood: низкая. Risk 1, P3, CONFIRMED (bin). repro: `REPRODUCTIONS/fc03_invalid_utf8_stream.sh`,
  LOGS/repro-fc03_invalid_utf8_stream.log; таблица b, строки u-* (класс fc03).
* proposed fix: записать в FORMAT.md §7 / HOST.md, что невалидный UTF-8 в запросе заменяется на U+FFFD, или
  читать строку байтами и отвечать `{"error": …}` (`String.fromUTF8?`).
* regression test: b_rfc_table `--strict` (строки u-*). fix commit: нет.

### FC-04. Запросы движка не строгие: неизвестные и опечатанные поля молча игнорируются
* module: `Figram/Engine.lean` `handleObj`/`decGuards`; class PRODUCT/DOC.
* reachable production path: любой хост, собирающий запросы вручную (не `figram_client.py`).
* trigger: `"gaurds"`/`"guard"` вместо `"guards"` → `{"need":["is_vip"]}` на каждом повторе (бесконечный цикл
  need у наивного хоста; `figram_client.py` защищён проверкой повторного need); мусор в `start`/`hello`
  (`"guards":5`, `"snapshot":null`, `"event":7`) принимается; при этом нелогичная асимметрия: неизвестные
  имена условий игнорируются, а не-bool значение у неизвестного имени отвергает весь запрос
  (`{"guards":{"other":1}}` на шаге без условий → error).
* impact: опечатка хоста не диагностируется, в отличие от строгого снимка (FORMAT §7: «неизвестное поле —
  ошибка»). Протокол не нарушается.
* likelihood: средняя для самописных хостов, нулевая для приложенного клиента. Risk 2, P3, CONFIRMED (bin).
* repro: `REPRODUCTIONS/fc04_request_not_strict.sh`, LOGS/repro-fc04_request_not_strict.log; d-engine строки
  extra-on-hello, typo-gaurds, typo-guard, start-with-junk, g-unneeded-nonbool, g-unknown-names.
* proposed fix: для каждого `op` проверять множество допустимых ключей (`id, op, snapshot, event, token, guards`)
  и отвечать `{"error":"неизвестное поле «…»"}`; либо явно записать в FORMAT §7, что запрос нестрогий.
* regression test: d_engine_requests (строки FC-04 фиксируют текущее поведение). fix commit: нет.

### FC-05. «Снимок строгий: ровно то, что разрешает схема» не выполняется для валидаторов на double
* module: `snapshot.schema.json`, `figram.schema.json` vs `numToNat`; class DOC.
* trigger: Python jsonschema (и любой валидатор на двоичных float, например ajv) принимает `seq: 1e-400` (0.0),
  `seq: 9007199254740990.5` (→ …990), `v: 1.0000000000000001`, `figram: 1.0000000000000001`, задержку `1e-400` /
  `9007199254740990.5`; точный декодер отвергает. Обратного направления (движок принимает то, что схема
  запрещает) не найдено ни в одной из 103 строк.
* impact: хост, проверяющий снимок схемой, может считать валидным снимок, который движок отвергнет
  (контролируемая ошибка запроса). Снимки, выданные самим движком, всегда целые.
* likelihood: очень низкая. Risk 1, P3, CONFIRMED (bin + jsonschema 4.26).
* repro: `REPRODUCTIONS/fc05_schema_float_precision.sh`, LOGS/repro-fc05_schema_float_precision.log;
  c-schema строки класса prec (6).
* proposed fix: оговорка в FORMAT §7 и в description схем: «целые сравниваются точно; валидаторы на double
  могут принимать нецелые значения, отличающиеся меньше чем на ULP».
* regression test: c_schema_table (строки prec). fix commit: нет.

### FC-06. Диагностика CLI и движка печатает имена из документа без экранирования
* module: `Main.lean` (`check`: `IO.println e`; `checked`; `readDoc` → `main` catch), `EngineMain.lean`
  `loadOrFail`; сообщения `FormatIO` («повторяющийся ключ «{k}»», «неожиданный символ «{c}»»), `Lint`, `Format.check`;
  class PRODUCT. (Протокол движка экранирует — проверено; `figram json` экранирует — AUDIT.md §4.)
* reachable production path: разработчик или CI запускает `figram check bot.json` / `figram-engine bot.json` на
  документе из редактора; автор документа (дизайнер, чужой экспорт) управляет именами.
* trigger: имя узла/события/условия/цели/ключа с `\u001b` (CSI/OSC: `\u001b[2K\u001b[1A` стирает строку,
  `\u001b]0;…\u0007` меняет заголовок терминала), `\n`, `\r`, `\u0000`.
* impact: подделка строк вывода: цель `"zz\nok\n"` даёт отдельную строку `ok` — `figram check f | grep -qx ok`
  проходит при rc 1; управляющие последовательности попадают в терминал/журнал CI; NUL проходит как байт 0
  (в Lean строка не усекается: `fmt` печатает `a\u0000HIDDEN`, хвост после NUL виден в выводе check).
  Код выхода остаётся верным (1). Тот же корень у `Api.check` («\n».intercalate errs) — передано ffi-go.
* likelihood: низкая (нужен злонамеренный или сломанный экспорт), последствия ограничены выводом. Risk 3, P3,
  CONFIRMED (bin).
* repro: `REPRODUCTIONS/fc06_cli_injection.sh`, LOGS/repro-fc06_cli_injection.log, LOGS/e-cli.log (8 из 9 строк).
* expected / actual: в выводе нет сырых C0/DEL, одна ошибка — одна строка / ESC, BEL, CR, NUL сырыми, лишние
  строки, exit 1.
* proposed fix: `Format.sanitize` (C0, DEL, C1 → `\u00XX`) в `readDoc`, `check`, `checked`, `loadOrFail`.
* regression test: `e_cli_names.py --strict` (177355a/897bfc8). fix commit: 4a38b91 (`Format.sanitize` в `readDoc`,
  `check`, `checked`, `EngineMain.loadOrFail`).
* retest (stage0 sanity, как FC-01): `e_cli_names.py --strict` rc 1 (8 из 9 строк) → 0 (0 из 9)
  (LOGS/fix-s0base-e-cli-strict.log → fix-s0build-e-cli-strict.log); контроль и протокол движка без изменений.
  На официальном тулчейне — BLOCKED.

### FC-07. Корневой раздел в сообщениях называется «» (и «bot»); пустое имя ребёнка совпадает с корнем
* module: `Lint.lean` (rootId = ""), `Format.check`; class PRODUCT (косметика).
* trigger: `{"initial":"b","children":{"a":{},"":{}}}` → «Начальный экран «b» раздела «» …», «Узел «» объявлен
  несколько раз» (дважды), рядом «вложенные узлы раздела «bot»: пустое имя».
* impact: непонятные сообщения дизайнеру; на корректность не влияет (документ отвергнут). Risk 1, P3, CONFIRMED.
* repro: `REPRODUCTIONS/fc07_root_name_in_messages.sh`, LOGS/repro-fc07_root_name_in_messages.log.
* proposed fix: в сообщениях Lint показывать «bot» для rootId, не дублировать «объявлен несколько раз».
* regression test: нет (косметика). fix commit: нет.

### Не находки (документированные решения, FORMAT.md §3, §5, §6) — из таблицы c
* struct (11 строк): схема строже декодера (пустые имена, `children: {}`, задержка ≥ 2^53), но `check` отвергает —
  согласовано на уровне `check`; `fmt` такие документы печатает (не проверяет V/границы) — так задумано.
* V (9 строк): V1–V5 схема выразить не может — FORMAT §3.
* dev (5 + 2 строк): повтор ключа (Python — «последний выигрывает»), непарный суррогат, показатель > 1000,
  глубина > 256 — документированные отклонения.
* footgun (3 строки, документировано): ключ `x-ui` внутри `children` — это новый узел-сообщение (states 1 → 2;
  FORMAT §5 «в словарях аннотаций нет»); неизвестный ключ вроде `"unless"` в варианте молча игнорируется (К5а/К5б:
  смысловые расширения обязаны идти через `requires`); обработчик `timer:t` на сообщении без таймера `t`
  принимается, если `t` есть где-то в дереве (V5 глобален).
* id ответа «по значению», а не побайтно (`1.5e3`→`15e2`, `0.1e1`→`1`, `"\u00e9"`→`"é"`, `-0`→`0`) и id:null при
  ошибке разбора — тот же корень, что ET-07 (engine-timers), дополнительные примеры в d-engine; новой находкой
  не считаю. Жетоны > 2^53, `5.0`, `"5"`, `1e19` — ET-12.

## 4. What I could not check / residual risk

* Официальный тулчейн (TOOLCHAIN_READY) так и не появился: исправления 4a38b91 проверены только сборкой
  stage0 (Lean 4.35.0 commit 5f4537b2, gcc, LOGS/fix-stage0-*.log) — это sanity, не CONFIRMED-fix; при сборке
  stage0 все модули доказательств скомпилировались, но `#print axioms` не запускался.
* Валидатор JS (ajv) не запускался — вывод о FC-05 для него по аналогии (двоичные float).
* Усечение имён с NUL на C-границе (`figram_ffi.c`, Go) — зона ffi-go; здесь показан только CLI и Lean-строка.
* Генератор `figram random` не порождает `variables`, `requires`, юникодные имена узлов; документы ≤ 15
  сообщений. Юникод и экранирование в именах проверены только в ключах/значениях аннотаций и в таблице b.
* Ожидаемые ответы d (65) записаны с baseline и проверены вручную по FORMAT §7; при этом 5 моих первоначальных
  предсказаний оказались неверны (выход из home отменяет `idle`; `resume` запоминает последнее место) — после
  разбора семантики ответы движка признаны верными.
* Не утверждаю, что найдены все ошибки: покрытие — таблицы и генераторы выше; доказательства К1 (`decode_encode`)
  относятся к модели, текстовый слой проверен только тестами.

Коммиты (ветка audit/formats-correctness): 897bfc8 — тесты tests/formats/ (a–e, run_all.sh);
177355a — регрессионные тесты FC-01/FC-06 (json_fuzz.py, fc01_lean_check.lean, --strict по находкам);
4a38b91 — исправление FC-01 и FC-06 (+ FORMAT.md). Патч: REPRODUCTIONS/fix-fc01-fc06.patch.

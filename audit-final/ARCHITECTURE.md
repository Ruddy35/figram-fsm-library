# Архитектура Figram: реальный путь исполнения, контракты, границы доверия

## 1. Путь исполнения

```
редактор (дизайнер) ──JSON-документ──▶ figram check / fmt (CLI)
                                         │
                       ┌─────────────────┴──────────────────┐
                       ▼                                    ▼
          Python/любой язык: процесс             Go/C/C++: в процессе
          figram-engine bot.json                 libfigram.a (figram_ffi.c + Api.lean)
          (stdin/stdout, строка JSON)            figram_init / figram_load_doc /
                       │                         figram_request_json / figram_unload
                       ▼                                    ▼
   Engine.handle: parse (FormatIO, НЕ доказан) → handleObj → decSnapshot/decEvent/decGuards
                       → missing (need) → Machine.step (доказан ≡ Chart.step при wf)
                       → renderCompact (НЕ доказан)
                       │
                       ▼
   ХОСТ (код разработчика, контракт HOST.md): блокировка на пользователя, отсев update_id,
   step → need → условия по данным → step, одна транзакция «снимок + update_id + outbox»,
   ack Telegram, исполнитель outbox (call с ключом идемпотентности, send, таймеры), планировщик таймеров
                       │
                       ▼
            внешний мир: Telegram, CRM, платежи (вне Figram)
```

Загрузка документа (`Engine.load`): `parse` → `decode` → `Doc.check` (внутри `lint` → `c.compile.wf`,
**первая** компиляция) → `toChart.compile` (**вторая** компиляция) → `wf && namesOK`. Шаг: линейный поиск
строки автомата (`m.rows.find?`) дважды (в `missing` и `step`), `s.state ∈ m.states` — несколько раз.

## 2. Компоненты и классификация файлов

| файл / каталог | класс | роль | что гарантирует / чем проверено |
|---|---|---|---|
| `Figram/Core.lean`, `CoreProofs.lean` | PRODUCT | ядро: `Machine.step`, снимок, таймеры, `realize` | `step_state_valid`, `step_guard_local` (доказано) |
| `Figram/Chart.lean`, `Compile.lean`, `Correct.lean`, `Run.lean` | PRODUCT | эталонная семантика схемы, компилятор | `compile_correct`, `run_correct`, `session_correct` при `wf` |
| `Figram/Lint.lean` | PRODUCT | проверки схемы (в т.ч. `compile.wf`) | тесты |
| `Figram/Format.lean`, `FormatProofs.lean` | PRODUCT | модель документа, `decode`/`encode`/`check` | `decode_encode`, `check_roundtrip` (модель `J`) |
| `Figram/FormatIO.lean` | PRODUCT | текстовый JSON: разбор и печать | **не доказано** (partial); фаззинг, таблицы RFC |
| `Figram/Engine.lean`, `EngineProofs.lean` | PRODUCT | протокол движка v1 | `decSnapshot_encSnapshot`, `step_snapOK`, `handle_step` (модель) |
| `Figram/Api.lean`, `ffi/figram_ffi.c` | PRODUCT (встраиваемый ABI) | C-ABI для Go/C/C++ | тесты C/Go, ASan/UBSan (см. ограничения FFI-15) |
| `Main.lean` (`figram`), `EngineMain.lean` (`figram-engine`) | PRODUCT | CLI и процесс движка; `--mutate` — тестовый режим в продуктовом бинарнике | тесты |
| `bin/figram`, `bin/figram-engine`, `bin/SHA256SUMS` | PRODUCT (артефакт выпуска) | предсобранные бинарники | CI: побайтно = официальная сборка baseline |
| `FORMAT.md`, `figram.schema.json`, `snapshot.schema.json` | PRODUCT (контракт формата) | формат документа, снимка, протокола | таблицы схема↔декодер |
| `HOST.md` | PRODUCT (контракт хоста) | правила для хоста: транзакции, идемпотентность, таймеры, обновление | модели хостов (host-perf, cross-verify), production-like хосты Go и Python |
| `Figram/Trace.lean`, `RandomDoc.lean`, `Mutate.lean`, `Examples.lean` | TEST (часть внутри продуктового бинарника) | эталонные сценарии, генератор документов, мутанты, `#guard` | — |
| `ffi/python-example/figram_client.py`, `protocol.py` | INTEGRATION | клиент для пула процессов движка | client_faults/contract/robustness |
| `ffi/go-example/main.go` (тип `Bot`) | INTEGRATION (если скопируют) / EXAMPLE | обёртка cgo + проверка сценариев | go test -race |
| `ffi/python-example/host_sim.py` | EXAMPLE / TEST | «эталонный хост» для HOST.md | см. HP-07: условия всегда `true` |
| `ffi/python-example/{conformance,mutation,random_docs,snapshot_fixtures,json_fuzz,harness_selftest,loadtest,client_*}.py`, `ffi/c_harness*.c` | TEST | обвязка | агент harness-ci |
| `verify.sh`, `verify-prebuilt.sh`, `.github/workflows/*` | TEST / BUILD | ворота качества, CI | T-01…: ложнозелёные места |
| `ffi/build.sh`, `lakefile.toml`, `lean-toolchain` | BUILD | сборка | FFI-02: только раскладка официального релиза |
| `README.md`, `AUDIT.md`, `REVIEW.md`, `VERIFY.log` | DOC | утверждения о гарантиях | сверены с фактами (ISSUES_TESTS.md, раздел DOC) |

## 3. Границы доверия

| граница | кто пишет данные | что может прийти | кто защищает |
|---|---|---|---|
| Документ бота | дизайнер/редактор (доверенный, но ошибающийся) | любые имена (в т.ч. управляющие символы), большие документы | `decode` + `check`; печать имён в CLI не экранировалась (FC-06, исправлено); время загрузки растёт сверхлинейно (P-06) |
| Событие Telegram → имя события | пользователь Telegram (недоверенный) | текст, callback_data, поддельные `timer:*` | хост: префиксы `btn:`/`msg:`; движок сам превращает `timer:*` в событие таймера с обязательным жетоном; хост обязан не принимать `timer:*`/`token` от пользователя (PH-05) |
| Снимок из БД хоста | хост (доверенный), но возможны откат, порча, старые версии | устаревший `seq`, неизвестные поля, дубли ключей | `decSnapshot` строгий; откат `seq` движок не видит — нужна верхняя отметка у хоста (P-05) |
| Условия (guards) | код разработчика | значения на момент шага | контракт HOST §1.3 (исправлен: барьер на пользователя) |
| Запрос к движку (строка) | хост | любой JSON | разбор без лимитов размера: квадратичные места (P-10) |
| C-ABI | встраивающий процесс | NULL, NUL внутри строк, конкурентные вызовы, вызов до init | частично: init потокобезопасен в integration (FFI-01), остальное — неписаный контракт (P-23) |

## 4. Владение, потоки, состояние

* **Состояние пользователя** целиком в снимке у хоста; движок — чистая функция (детерминизм подтверждён:
  I10, 1768 проверок; Go- и Python-хост дали одинаковые переходы на ≈10 000 обновлений).
* **Дедупликация** — у хоста (`update_id`), не у движка. Идемпотентность внешних действий — ключ
  `пользователь:seq:номер` (HOST §1.4), **уникален только пока `seq` не повторяется** (P-01).
* **Процессный движок** однопоточный, ответы строго FIFO; параллельность — пулом процессов (клиент Python).
* **Встроенный движок**: автомат после загрузки помечается `lean_mark_mt` (атомарные счётчики ссылок) и
  используется из любых потоков; `figram_unload` освобождает его, вызывающий обязан не вызывать его
  параллельно с запросами (Go-обёртка — `RWMutex`). Экспортные функции Lean забирают владение аргументами;
  прослойка делает `lean_inc` на время вызова — проверено (ffi-go, раздел 1). `figram_init` до integration
  не был потокобезопасен (FFI-01/NP-04), запускает поток libuv и io_uring, резервирует ≈2,2 ГБ адресного
  пространства, меняет `sigaltstack` вызывающего потока, игнорирует SIGPIPE (FFI-09/NP-02).
* **Таймеры**: планирует хост; движок выдаёт `start_timer` с жетоном = `seq` перехода и принимает
  срабатывание только с текущим жетоном. `cancel_timer` жетона не несёт.

# fix-ffi — отчёт

Ветка `fix/ffi`, база `fix/base` (8be73ac), HEAD `9ba1932`. Тулчейн Lean 4.35.0 собран из исходников (gcc/libstdc++). Логи: `/home/user/figram-audit/fixes/ffi/LOGS/NN-*.log`; в каждом записаны команда, время и код выхода.

## Таблица

| item | change | commit | regression test | RED log | GREEN log | verdict |
|---|---|---|---|---|---|---|
| B-01 | см. раздел B-01 | 9a90583, 189f2ef (revert), 82ac414 | `ffi/audit/b01_build.sh`; verify.sh без CGO_LDFLAGS | 12-b01-build-RED-base, 20-build-sh-RED-after-revert, 21-b01-RED-after-revert | 22-b01-GREEN, 50-verify-gate-nocgo (код 3, только хеш), 53-verify-gate-cgo-brief | FIXED (официальная раскладка не проверена локально) |
| P-09 | C++-модуль `ffi/figram_guard.cpp`, проверка запаса RLIMIT_AS, `figram_last_error()` | b7f7476 | regress.sh: P-09 1,5 ГБ / окно 1 ГиБ / 2,1 ГБ / 3 ГБ; Go `TestFixInitUnderRlimitAS` (запас +1 ГиБ и 3 ГБ); host-go np01 | 10-regress-RED-base, 51-RED-base-final-tests (C: 134 SIGABRT и «INTERNAL PANIC»; Go 3 ГБ: SIGABRT) | 39/44-regress-GREEN, 40-go-test-GREEN, 52-hostgo-np01-GREEN, 38 (контроль без проверки запаса — паника) | FIXED |
| P-21 | ленивая потокобезопасная init везде, NULL → ошибка, ячейки-дескрипторы с карантином, функции `_n`, `load_doc_ex[_n]`, экранирование NUL в выводе | baf6ce6, b7f7476 | regress.sh: lazy, lazy_race ×300, null_safe, null ×5, lifetime, lengths | 10, 51 (SIGSEGV 139; MISSING) | 44, 45-sanitizers-GREEN | FIXED (оставшееся UB описано) |
| P-18 (FFI) | строгий UTF-8 в C, `figram_set_max_request` (1 МиБ), пустой запрос → parse_error, тексты как у движка | b7f7476, 349941d | regress.sh: utf8_strict, limit; c_harness; Go `TestFixRequestTooLarge` | 10, 13-c-harness-RED-base, 51 | 44, 50, 53 | FIXED |
| P-22 | сохранение/восстановление SIGPIPE и sigaltstack вокруг init; остальное — в документации | b7f7476, 5a87c81 | regress.sh: signals default / custom | 10, 51 | 44 | FIXED (+DOC) |
| P-23 | рекомендация в figram.h и README; бенчмарк | 5a87c81 | doc_check.sh; бенчмарк 55 | — | 54-doc-consistency, 55-bench-base-vs-fixed | FIXED-DOC |
| I-17 | Go: `Init()` (sync.Once) вызывается из `Load`; ошибка init — ошибка `Load` | 5238930 | `TestFixLazyLoadWithoutInit` | 11-go-fix-tests-RED-base, 51 (SIGSEGV) | 40, 41-go-test-race-GREEN, 42-go-init-race-100 | FIXED |
| I-15 | сохранено (Go отвергает NUL; C `_n` тоже) | 5238930 | `TestAuditLoadNUL`, `TestFixLoadReason` | 11 | 40 | сохранено |
| checkReply / C-обвязка | ошибки `{id,error,code}`, коды из закрытого списка, id null только у parse_error/too_large, hello с `mutant`; ожидания c_harness | fb3cacc, 5238930 | `TestFixCheckReplyProtocolChanges`, c_harness | 11, 13 | 40, 50 | FIXED (переключатель legacy) |
| D-05 | докстринг `Api.load`/`loadEx` (`lean_mark_mt`); README без «только libc», таблица архивов, .so не поддерживается | baf6ce6, 5a87c81 | `ffi/audit/doc_check.sh` | (утверждения были в README и Api.lean) | 54 | FIXED-DOC (REVIEW.md/AUDIT.md — не моя зона) |
| T-12 | README и figram.h: ASan/valgrind/TSan не видят кучу Lean, оракул — стресс; шаг ASan переименован; `sanitizers.sh`, `mutants.sh` | b7f7476, d8ed7a8, 5a87c81, 476670c | doc_check.sh, mutants.sh | — | 47-mutants-caught, 54 | FIXED-DOC |

## B-01

`ffi/build.sh` определяет раскладку тулчейна по наличию `$PREFIX/lib/libc++.a`.

- **Официальный релиз.** Строка копирования архивов осталась прежней, флаги те же, что раньше были зашиты в main.go и verify.sh: `-Wl,-Bstatic -lc++ -lc++abi -luv -lgmp -Wl,-Bdynamic -lm -ldl -lpthread -lrt`. Добавлены сборка `figram_guard.cpp` (`leanc -c`) и флаг `-fexceptions` у `figram_ffi.c`. Оба файла собираются без системных заголовков. Компиляцию с `clang -nostdinc` и встроенными заголовками clang, как это делает leanc официального релиза, проверяет `b01_build.sh`. Саму официальную раскладку проверить локально нельзя: в CI это первый прогон.
- **Сборка из исходников.** Системные статические `libuv.a` и `libgmp.a` находятся через `$CC -print-file-name`; если их нет, выдаётся понятная ошибка. Рантайм C++ — системный `-lstdc++`. Пустых заглушек больше нет.

Скрипт пишет:
- `ffi/out/LDFLAGS` — полные флаги хоста с абсолютными путями;
- `ffi/out/LDFLAGS.runtime` — те же флаги без `libfigram.a`;
- `ffi/go-example/zz_figram_ldflags.go` — генерируется, в `.gitignore`.

Поэтому main.go больше не содержит `-lc++`, и `go build` работает без переменных окружения. Свою обёртку на Go собирают так: `CGO_LDFLAGS="$(cat ffi/out/LDFLAGS)" go build`, хост на C — `cc host.c $(cat ffi/out/LDFLAGS)`. verify.sh и `ffi/audit/build_audit.sh` берут флаги из этих файлов.

Зависимости на gcc-тулчейне (ldd): libstdc++.so.6, libgcc_s.so.1, libm, libc. Это записано в README.

## P-09

`figram_init` вызывает инициализацию рантайма и модуля через `figram_guard_call` (`ffi/figram_guard.cpp`): `catch (const std::exception&)` и `catch (...)` превращаются в код 2 и текст ошибки. `std::exception` объявлен вручную, если `<exception>` нет — тот же ABI Itanium, что у libstdc++ и libc++.

Замеры:
- Рантайму нужно около 2,15·10⁹ байт сверх занятого: арена mimalloc 1 ГиБ и стек потока libuv 1 ГиБ. В C и в Go одинаково.
- При запасе ровно около 1 ГиБ арена занимает всё, и следующий `mmap(64K)` получает ENOMEM. Рантайм тогда вызывает `INTERNAL PANIC: out of memory` и `exit(1)`, это не перехватить. Поймано тестом Go (strace, gdb), контроль — лог 38.

Поэтому на Linux при RLIMIT_AS с запасом меньше 2·10⁹ байт рантайм не запускается вовсе: `figram_init` возвращает 2 с понятной причиной. При запасе от 2·10⁹ до 2,15·10⁹ ловится исключение «failed to create thread».

Перебор лимитов от 20 МБ до 2,5 ГБ (C) и запасов от 0 до 2,3 ГБ (Go): ни SIGABRT, ни паники.

Коды `figram_init`: 0 — готово; 1 — модуль Lean вернул ошибку; 2 — рантайм не запустился. Отказ окончателен; все функции после него возвращают ошибку, хост остаётся жив.

## P-21

- **Ленивая инициализация.** Каждая точка входа сначала проверяет состояние одной атомарной загрузкой; инициализация по-прежнему однократная (CAS).
- **NULL.** `check`/`fmt` возвращают текст `error: …`; `load` возвращает NULL с причиной; `request` возвращает `{"id":null,"error":…,"code":"bad_request"}`; `unload(NULL)` и `free(NULL)` ничего не делают.
- **Дескрипторы.** Дескриптор — указатель на ячейку из собственных блоков прослойки. Блоки не освобождаются, поэтому проверка «это наша ячейка» безопасна для любого указателя. Выгруженная ячейка проходит карантин из 1024 выгрузок. Обнаруживаются: чужой указатель, запрос к выгруженному автомату (`bad_request`), повторная выгрузка (ничего не делает, причина — в `figram_last_error`).
- **Новые функции.** `figram_{check,fmt}_json_n`, `figram_load_doc_n`, `figram_load_doc_ex[_n]`, `figram_request_json_n` — с длиной, встроенный NUL отвергают. `figram_load_doc_ex` возвращает причину (новый экспорт Lean `Api.loadEx`).
- **Вывод.** Сырой NUL в тексте ошибки (например, имя ключа `a\u0000b`) записывается как `\u0000`.
- **Остаётся UB** (описано в figram.h и README):
  - выгрузка одновременно с запросами к тому же дескриптору;
  - дескриптор, выгруженный больше 1024 выгрузок назад;
  - строка без NUL в вариантах без `_n`;
  - вызовы после `fork()`.

## P-18 (FFI) и протокол

- **Строгий UTF-8.** Правила те же, что у `validate_utf8_one` в рантайме Lean. Запрос с невалидным UTF-8 получает `parse_error` с текстом «запрос не в кодировке UTF-8»; документ — `error: документ не в UTF-8 (смещение N)`.
- **Предел размера.** `figram_set_max_request(n)`: по умолчанию 1048576, 0 — без предела, функция возвращает прежнее значение. Более длинный запрос получает `too_large` с текстом «запрос длиннее предела N байт», как у движка.
- **Пустой запрос** получает `parse_error`; в процессе такие строки пропускаются — задокументированная асимметрия.
- **id в ответах C-уровня всегда null.** Процесс на fix/protocol отдаёт эхо id. На FFI ответ и так относится к этому вызову.
- **Go-обёртка** передаёт запрос с длиной (`figram_request_json_n`), без копии через `C.CString`, и даёт `SetMaxRequest`.

## P-22, P-23

- `figram_init` сохраняет диспозицию SIGPIPE и `sigaltstack` вызвавшего потока и восстанавливает их. Буферы непрозрачные, системные заголовки не нужны.
- Остальные побочные эффекты описаны в figram.h и README «Встраивание»: поток libuv и io_uring, резерв около 2,2 ГБ адресного пространства, около 8 файловых дескрипторов, обработчик SIGSEGV/SIGBUS, требование к RLIMIT_AS. Ограничение: если другой поток хоста меняет SIGPIPE одновременно с инициализацией, его изменение может потеряться.
- P-23: рекомендация «один дескриптор на рабочий поток» для хостов на C. Бенчмарк (лог 55): общий дескриптор 131–147 тыс. запросов/с, отдельные — 163–187 тыс.; до исправлений 126–158 и 161–183 — разница в пределах шума.

## I-17, Go-пример, C-обвязка

- **Go.**
  - `Init()` (sync.Once) копирует `figram_last_error` в том же вызове cgo: текст хранится в памяти потока.
  - `Load` вызывает `Init` и использует `figram_load_doc_ex_n`: ошибка содержит причину, NUL отвергается.
- **checkReply.**
  - Ошибка — это ровно `{id,error,code}`, где `code` ∈ {`parse_error`, `too_large`, `bad_request`, `bad_snapshot`, `seq_overflow`}.
  - id null допускается только у `parse_error` и `too_large`.
  - `hello` может содержать непустую строку `mutant`.
  - `start` с полем `seq` проходит через `Request` без изменений.
  - Пока движок этой ветки не выдаёт коды, принимается и форма `{id,error}` (`legacyErrorWithoutCode=true`). После слияния с fix/protocol поставить false.
- **C-обвязка `ffi/c_harness.c`** проверяет коды `parse_error` и `too_large` с id null для ошибок C-уровня.
- **verify.sh:**
  - новый шаг «C-ABI»: `regress.sh` — не меньше 24 проверок и 0 провалов, плюс `figram.h` компилируется как C99 `-pedantic` и как C++ с `-Werror`;
  - в прогон `go test -race` добавлены 4 теста TestFix;
  - отдельно, без `-race`, — `TestFixInitUnderRlimitAS`.

## D-05, T-12

- **Документация.** Докстринги в `Api.lean` исправлены. В README новый раздел «Встраивание»:
  - сборка, LDFLAGS и таблица архивов по тулчейнам;
  - `.so` не поддерживается: `libleanrt` собран с local-exec TLS, `R_X86_64_TPOFF32`;
  - контракт C-ABI, побочные эффекты, RLIMIT_AS, потоки;
  - санитайзеры слепы к куче Lean.
- **Строки таблицы проверок.** Строка «C-прослойка» теперь говорит, что под ASan и UBSan инструментирована только прослойка; добавлена строка «C-ABI».
- **Санитайзеры** (`ffi/audit/sanitizers.sh`). Под TSan подтесты с одновременными загрузками дают сотни ложных «гонок» из-за переиспользования страниц mimalloc (известно из FFI-15), поэтому `lazy_race` и стресс с потоками загрузки под TSan не запускаются. Оракул — `stress.c`.

## Прочие проверки

- **Шлюз.** `verify.sh` целиком; внутри — `strict_gate.sh` (PASS), клиенты Python, нагрузка, Go. Оба прогона — код 3 с одним отступлением.
- **Регрессия.** `ffi/audit/regress.sh`: 24 из 24.
- **Санитайзеры.** `sanitizers.sh`: 20 из 20.
- **Чувствительность.** `mutants.sh`: 10 из 10 мутантов пойманы.
- **Go.** `go test` и `go test -race` — PASS; `TestAuditInitRace` — 0 падений из 100.
- **Документация.** `doc_check.sh` — согласовано; `figram.h` и `figram_ffi.c` экспортируют одни и те же 15 функций.
- **Не запускал отдельно:** `audit/core-compiler`, `engine_timers`, `tests/formats`. Lean я менял только добавлением экспорта в `Api.lean`, а модули бинарников `figram` и `figram-engine` не трогал.

## Риски и чего не сделал

1. **Официальная раскладка не проверена.** Её проверит первый прогон CI: сборка `figram_guard.cpp` через leanc (clang `-nostdinc`) и компоновка с `libc++abi.a`. Компиляцию с `-nostdinc` я проверил локальным clang 18, компоновку с libc++ — нет.
2. **Порог 2·10⁹ байт** — замер на этом тулчейне. Он выведен из констант исходников Lean (стек 1 ГиБ, арена 1 ГиБ) и переопределяется макросом `FIGRAM_INIT_AS_HEADROOM`. Если официальный рантайм потребует меньше, отказ возможен только в узкой полосе между реальной потребностью и порогом.
3. **Нехватка памяти во время работы** (огромные запросы, P-10) по-прежнему завершает процесс через «INTERNAL PANIC», а не возвращает ошибку. От огромных запросов защищает только `too_large`.
4. **Эха id на FFI нет** (асимметрия задокументирована). Полного совпадения с процессом можно добиться после слияния: экспортировать `Engine.handleBytes` из fix/protocol, например как `figram_request_bytes` в `Api.lean`.
5. **Не правил** `REVIEW.md:28` и `AUDIT.md:55` — это зона fix-docs. `figram_shutdown` не добавлял: рантайм Lean не умеет останавливаться.
6. **Слияние:** конфликт только в README с fix/docs (одна строка таблицы), способ решения описан выше.

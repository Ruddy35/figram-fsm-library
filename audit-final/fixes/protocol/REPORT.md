# fix-protocol — отчёт

Ветка `fix/protocol`, база `fix/base` (8be73ac). Логи: `/home/user/figram-audit/fixes/protocol/LOGS/`. Базовые бинарники для сравнения: `/home/user/figram-audit/fixes/protocol/base-bin/`.

| item | изменение | commit | регрессионный тест | RED log | GREEN log | вердикт |
|---|---|---|---|---|---|---|
| P-10 (FS-1/2/3, ET-08) | FormatIO: разбор по байтам String (`getUTF8Byte`/`extract`, без `List Char`), повтор ключа — хеш-множество (линейный поиск до 8 ключей), цифры и печать чисел делением пополам, `escape` через `push` | 9e920f9 (тесты), 653be32 | `tests/formats/f_parser_scaling.py --check`; `f_parser_diff.py` (60 000 входов, старый против нового) | 04-RED-parser_scaling-base | 08-GREEN-parser_scaling, 23-GREEN-parser_diff-final | FIXED |
| P-20 (коды ошибок) | `ErrCode`, `handleObj : Except (ErrCode×String) J`, `errorJ` | 7aeeaf8, d4befe9 | protocol_edges, d_engine_requests, snapshot_fixtures (bad_snapshot), protocol.py `check_error` | 01-, 02-, 03-RED-*-base | 28-GREEN-* | FIXED |
| P-16 (эхо id, FIFO) | `echoId` для parse_error/too_large; порядок ответов описан в FORMAT §7 | d4befe9, 60fe914 | protocol_edges (сырые строки), d_engine (id-dup, g-dup, s-dup-*, two-objects), ptsuite E2c | 01-, 02-, 11-RED | 28-, 33-GREEN | FIXED |
| P-19 / FC-04 / ET-12 (строгие запросы, жетоны, тексты) | `checkFields` (вне `handleObj`), `decEvent`: непустое событие, непустое имя таймера, `token` только у таймера и 0…2^53−1 по значению; новые точные тексты | d4befe9 | protocol_edges, d_engine, ptsuite T2/T3, walk ptdriver | 01-, 02-, 11-RED | 28-, 12-, 33-GREEN | FIXED |
| P-01 (часть в коде) | `start` с `seq` (`startAt`; доказаны `startAt_zero`, `startAt_snapOK`) | d4befe9 | protocol_edges (start seq), check_host_contract с процедурой «start с верхней отметкой» | 09-RED-host_contract_start_seq-base | 09-GREEN, 29-GREEN | FIXED (текст HOST §4 — fix-docs) |
| Предел размера (PROTOCOL 5) | figram-engine читает побайтно, `--max-request` (1 МиБ, 0 — без предела), лишнее не держит в памяти (сохраняется только 64 КиБ начала) | d4befe9 | protocol_edges (1 МиБ, 1 МиБ+1, 64, 0) | 01-RED | 28-GREEN | FIXED (процесс); FFI — fix-ffi |
| P-18 / FC-03 / FS-5 (UTF-8) | процесс: байты → `String.fromUTF8?`, иначе `parse_error`; `figram json` тоже | d4befe9, 98565cb | protocol_edges, d_engine u-*, b_rfc_table TB, ptsuite E7 | 01-, 02-, 11-RED | 24-formats, 28-, 33-GREEN | FIXED (процесс); FFI — fix-ffi |
| P-17 / ET-09 (мутанты) | только при `FIGRAM_ALLOW_MUTANTS=1`, предупреждение в stderr, `hello.mutant`, отказ при номере/имени вне автомата, повторная проверка wf/namesOK | d4befe9, db974ec | protocol_edges (CLI), ptsuite M1/M2 | 01-, 11-RED | 28-, 33-GREEN | FIXED |
| INT-08 | FORMAT §7, правило 3 — по условию HOST §1.3 | 60fe914 | `tests/formats/g_protocol_docs.py` | 13-RED-protocol_docs-baseFORMAT (27 отказов) | 13-GREEN-protocol_docs | FIXED-DOC |
| FC-02 (−0) | уже описан в FORMAT §3 (проверено), не менялся | — | b_rfc_table n-neg-zero-* | — | 24-formats | FIXED-DOC (было) |
| FC-05 | FORMAT §7: валидаторы на double шире движка | 60fe914 | — (документ) | — | — | FIXED-DOC |
| Тексты ошибок (ET-12) | текст для человека оставлен, добавлен код, путаные тексты заменены | d4befe9 | d_engine (точные строки) | 02-RED | 28-GREEN | FIXED |

Прочие коммиты:
* 98565cb — адаптация наборов: engine_timers (валидатор `code`, walk ожидает `bad_request`, check_host_contract исполняет процедуру `start` с `seq`), core-compiler (`FIGRAM_ALLOW_MUTANTS`), formats `run_all` (ожидания `--strict` для исправленных находок = 0, новые шаги f/g), `verify*.sh` (env для мутаций, нижние границы).
* 259b6c6 — проверка согласованности документации.
* 1b8e691 — самопроверка обвязки портит `code`: 37 проверок, нижняя граница 37.
* 481646f — случай `drop-call:nope`; оракул core-compiler моделирует границы жетона.

## Замеры P-10 (до → после, `nice -n 10`, RLIMIT_AS 6 ГиБ, пик RSS дочернего процесса, нижняя граница метода 10 МиБ)

| вход | до | после |
|---|---|---|
| `figram json`, объект 16k / 32k ключей | 1,21 / 5,62 с | 0,07 / 0,10 с |
| целое / дробь, 160k цифр | 11,9 / 11,1 с | 0,05 / 0,08 с |
| строка 5 / 20 МБ | 1,75 с, 275 МБ / 6,9 с, 996 МБ | 0,36 с, 54 МБ / 0,9 с, 122 МБ |
| движок: guards 32k ключей | 3,97 с | 0,06 с |
| движок: `id` 160k цифр (эхо) | 11,4 с | 0,07 с |
| движок: `event` 20 МБ (`--max-request 0`) | 6,0 с, 977 МБ | 1,6 с, 70 МБ |

Цена: процесс читает запрос по байту, иначе нельзя совместить предел размера, строгий UTF-8 и немедленный ответ (`read n` ждал бы, пока придут n байт). На 100 000 шагах (запрос 134 байта) было 1,90 с, стало 2,25 с — примерно +3,5 мкс на запрос. Это записано в README.

## Изменения поведения и протокола
* Протокол по-прежнему версии 1; все изменения перечислены в PROTOCOL_CHANGES 1–8 и в разделе «Deviations (protocol agent)».
* Ранее принятые запросы теперь отвергаются с `bad_request`:
  * лишние поля (`gaurds`, мусор в `start`/`hello`);
  * `token` больше 2^53−1, `token` у обычного события;
  * `"event": ""`, `"timer:"` без имени.
* golden.jsonl не изменился (cmp в verify).
* Сырой NUL и invalid UTF-8 в потоке больше не превращаются в U+FFFD.

## Риски
* Строгость может сломать самописные хосты, которые шлют лишние поля (так и задумано).
* До слияния с fix-ffi verify.sh на этой ветке красный на Go-тесте `TestAuditRequestBadOp`.
* Путь FFI ещё без предела размера и без проверки UTF-8 (зона fix-ffi; для них есть готовая `Engine.handleBytes`).
* Вероятны конфликты слияния с fix-docs в README и FORMAT. Свои правки README я ограничил строками, которых fix-docs не трогает.

## Что не сделано или вне моей зоны
* HOST.md §4/§5 (`start` с `seq`, коды ошибок) — fix-docs; в их ветке это уже есть.
* Go `checkReply`, предел и UTF-8 на пути FFI, AUDIT.md:80 (U+FFFD для C/Go) — fix-ffi.
* core-compiler `mutants` (490 неэквивалентных выживших) красный и на fix/base (лог 25b) — существующий дефект набора или ядра, не протокола.
* Ветку отказа повторной проверки wf/namesOK для мутанта тестом не покрыть: все 107 мутантов примеров её проходят.

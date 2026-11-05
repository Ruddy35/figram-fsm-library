# Аудит ffl-go 0.3.0: проверки исправленного кода, CI и релиз

Журналы — `LOGS/orchestrator/ffl-go-030/` (файл `*.log`: команда, каталог, время, код выхода;
рядом `*.stdout`, `*.stderr`). Машина: 4 vCPU, Linux 6.18.44; Go 1.24.7, Python 3.13.16, gcc
13.3.0. Дерево merge-коммита 777d04c (релиз v0.3.0) равно дереву 361815c
(`c47c1e08c31274c231a3ace9407efcaa9e3c1cde`), поэтому проверки 361815c относятся и к релизу.

## Локальные проверки исправлений (коммит 361815c, рабочая копия `/home/user/ffl-go`)

| Журнал | Команда | Код | Результат |
|---|---|---|---|
| `vet-2` | `go vet ./...` | 0 | |
| `test-2` | `go test -count=1 -v ./...` | 0 | 35 тестов PASS, 0 FAIL |
| `race-2` | `go test -race -count=1 ./...` | 0 | |
| `consumer-3` | `bash scripts/consumer-test.sh` | 0 | отдельный модуль: обычная сборка, vendored, `--gc-sections` (7 314 016 байт) |
| `update-lib-check-2` | `bash scripts/update-lib.sh --check` | 0 | «the tree equals release v0.3.0» |
| `mutants-3` | `python3 scripts/mutants.py` | 0 | «mutants 37, survived 0» |

Промежуточные красные прогоны сохранены как есть:
- `mutants-2` (код 1, «survived 1»): новый мутант «поля результата в другом порядке» был
  записан позиционным литералом структуры и не собирался, а сборка не считается обнаружением.
  Мутант переписан, и `mutants-3` — 37 из 37.
- `consumer-2` (код 1): `scripts/consumer-test.sh` ждал старый порядок полей `Result`
  (`actions` первым). Ожидание обновлено на порядок движка, и `consumer-3` — код 0.

Проверки до аудита, на 51c2312: `vet`, `test`, `race`, `consumer`, `update-lib-check`, `mutants`
(23 мутанта) — все с кодом 0.

## Мутанты агента на исправленном коде

`tools/agent_mutants_fixed.py` — список агента (`agents/fflgo/q5/my_mutants.py`, на 51c2312
выжили 12 из 18) на экспорте 361815c, с тем же критерием (`go test -count=1 ./`). Журнал
`agent-mutants-fixed`, код 0.

| Итог | Мутанты |
|---|---|
| контроль без изменений | PASS |
| убиты (14 из исходного списка) | проверки `timers`, значений `resume`, `v`, `at`, `seq`, `states` в hello, токена `start_timer` (все — `TestCheckReply`); обратный порядок `pending`; потеря условий `Timer` (`TestTimerGuards`); отказ `Outcome` при `:` в имени действия (`TestOutcomeColonAction`); `detached` у каждого вызова; протокол hello как `states`; `SetMaxRequest` без аргумента; потеря таймеров в `toSnapshot` |
| неприменимы (текста больше нет в коде) → переписаны под новый код и убиты | текст после ответа (`TestDecodeJSON`); элементы `need` без проверки (`TestCheckReply`); повторы в `need` снова принимаются (`TestCheckReply`) |
| выжили (2) | «снимок теряет `v`» и «снята защита `Decide` от повторного вопроса» — агент отнёс оба к практически эквивалентным: движок не пишет `v` в снимки (0 из 99 248 ответов корпуса агента `agents/fflgo/corpus/valid.jsonl.gz`; по `docs/format.md` отсутствие `v` значит 1) и не спрашивает повторно об известном условии |

## Наблюдение при архивации (P3, не исправлено)

После исправления P3-1 `decodeJSON` отрезает хвост ответа функцией `unicode.IsSpace`, а не
только пробельными символами JSON (пробел, `\t`, `\n`, `\r`). Поэтому ответ, за которым следуют
`\v`, `\f`, U+0085 или U+00A0, принимается; `json.loads` Python такой ответ отвергает
(`ws-probe-go`, `ws-probe-py`, код 0; пробы — `tools/ws-probe/`). Практического следствия нет:
ответ пишет библиотека в том же процессе, а движок после JSON выводит только `\n` или ничего.
Исправление в одну строку (обрезать только `" \t\r\n"`) — кандидат для ffl-go 0.3.1.

## CI и релиз

| Что | Запуск | Коммит | Итог |
|---|---|---|---|
| ci, push ветки | 41941023187 (#13) | 361815c | success, 6 заданий |
| ci, pull_request (PR #3) | 41941027667 (#14) | 361815c | success, 6 заданий |
| ci, push `master` после мержа | 41941458376 (#15) | 777d04c | success |
| release (workflow_dispatch) | 41947432671 | 777d04c | success; черновик v0.3.0 с CHANGELOG и provenance; проверки на go1.27.3, «mutants 37, survived 0» |
| ci, push тега `v0.3.0` | 41948350922 (#16) | 777d04c | success |

Релиз [v0.3.0](https://github.com/Ruddy35/ffl-go/releases/tag/v0.3.0) опубликован
2026-11-05T01:24:49Z; тег `v0.3.0` → 777d04c.

## Проверка опубликованного модуля

`tools/proxy-consumer/check.sh`: новый модуль, пустой `GOMODCACHE`, `GOPROXY=https://proxy.golang.org`,
`GOSUMDB=sum.golang.org`, `go get github.com/Ruddy35/ffl-go@v0.3.0`, сборка примера из README
(`tools/proxy-consumer/main.go`), запуск на `examples/pizza.bot.json` Figram. Журнал
`proxy-consumer`, код 0:

```
go.sum:github.com/Ruddy35/ffl-go v0.3.0 h1:5jxVbswq2PNMsXCLe7REcfMxVIuAsO2wku1Pipaqxso=
bundled 0.3.0 protocol 3
{"at":"catalog","back":"menu","resume":{"order":"catalog"},"seq":2}
send catalog
```

## Документация Figram

`docs/embedding.md`, «Go» → `go get …@v0.3.0` (5bf932c, PR #12, смержен как af0db04):
`python3 ci/check_docs.py` — журнал `figram-check-docs`, код 0, «check_docs: 0 problem(s)»; CI
PR #12 — 20 из 20 заданий success.

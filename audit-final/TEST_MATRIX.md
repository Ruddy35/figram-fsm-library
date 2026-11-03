# Проверки итогового кода (коммит 598fec6)

## Локальная полная проверка

Скрипт `tools/gate_v030b.sh`; журналы `LOGS/orchestrator/v030-final-gate2/` (файл `*.log`: команда,
каталог, время, код выхода; рядом `*.stdout`, `*.stderr`), сводка `SUMMARY.txt`. Рабочее дерево
чистое (`STATUS.txt` пуст). Машина: 4 vCPU, Linux 6.18.44; Lean 4.35.0 (commit eb6cafb7,
собран из исходников), gcc 13.3.0, Go 1.24.7, Python 3.13.16, jsonschema 4.26.0.

| Шаг | Команда | Код | Результат |
|---|---|---|---|
| 01 | `lake build` | 0 | без предупреждений |
| 02 | `bash ffi/build.sh` | 0 | `ffi/out/libfigram.a` |
| 03 | `lake env lean tests/proofs/Statements.lean` | 0 | формулировки закреплены |
| 04 | `./verify.sh` | 0 | «ALL CHECKS PASSED» |
| 05 | `bash tests/formats/run_all.sh` | 0 | неожиданных кодов выхода 0 |
| 06 | `bash tests/compiler/run_all.sh` | 0 | все шаги ok, включая красные контроли |
| 07 | `bash tests/capi/sanitizers.sh` | 0 | 22 PASS, 0 FAIL |
| 08 | `bash tests/capi/mutants.sh` | 0 | «all mutants caught» |
| 09 | `bash tests/proofs/controls.sh` | 0 | 0 failures |
| 10 | `go test -count=1 ./...` (clients/go) | 0 | |
| 11 | `go test -race -count=1 ./...` | 0 | |
| 12 | `python3 ci/check_docs.py` | 0 | 0 problems |
| 13 | `./verify-faults.sh prebuilt` | 0 | «the gate is red on every fault and green without them» |

Ключевые числа из `04-verify.log`:

| Проверка | Результат |
|---|---|
| Закреплённые теоремы | 99, только стандартные аксиомы; формулировки совпадают с `THEOREMS.expected` |
| Строгая проверка теорем | `STRICT GATE: PASS` (365 теорем исходников, окружение, `leanchecker`) |
| Покрытие вариантов стрелок | pizza 53 из 53, features 75 из 75 (`figram trace … --directed`) |
| Сверка сценариев с движком | 17 772 запроса, 0 расхождений |
| Случайные документы | 100 документов, 92 439 запросов, 0 с расхождениями |
| Мутации машины | 211 из 211 обнаружено |
| Контракт хоста | «contract holds», 4 контроля красные, контроли потерянного снимка красные |
| Пограничные случаи протокола | 252 ответа, 0 расхождений |
| Линейность по размеру снимка | 9 случаев, 0 провалов |
| Предупреждения `figram check` | 29 случаев, 0 провалов |
| Самопроверка стенда | «the harness catches corruption» |
| Go, много горутин | до 615 горутин, 24 729 запросов, 0 расхождений |

## CI

Прогон 41707449295 (run 41, push 598fec6): conclusion success —
https://github.com/Ruddy35/figram-fsm-library/actions/runs/41707449295

## Ранние прогоны (для истории)

- `LOGS/orchestrator/v030-pre/`, `v030-pre2/` — `verify.sh` до итогового аудита (первый красный:
  самопроверка стенда поймала пробел ST-1; второй — «ALL CHECKS PASSED»);
- `LOGS/orchestrator/v030-final-gate/` — прогон, остановленный вручную (во время него был
  изменён `verify.sh`), не учитывается;
- `LOGS/orchestrator/scratch/` — отдельные запуски оркестратора (строгая проверка, контроли
  доказательств, мутации, самопроверка, `host_sim`, `check_docs`, TSan `init_race`).

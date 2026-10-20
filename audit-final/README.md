# audit-final — аудит Figram

Начните с **EXECUTIVE.md**. После аудита найденные проблемы исправлены: итоговый статус каждого пункта — **FIXES.md**
(статусы в реестрах ISSUES_* сняты на момент аудита).

| файл | содержание |
|---|---|
| EXECUTIVE.md | вердикт READY / CONDITIONAL / NOT READY по режимам, P1, что исправлено, что не проверено |
| ARCHITECTURE.md | реальный путь исполнения, классификация файлов PRODUCT/INTEGRATION/EXAMPLE/TEST/DOC/BUILD, границы доверия, владение |
| ISSUES_PRODUCT.md | реестр дефектов продукта P-01…P-25 (дедуплицирован, с источниками и независимыми подтверждениями) |
| ISSUES_INTEGRATION.md | клиент Python, Go-обёртка, `host_sim.py` (I-01…I-18) |
| ISSUES_TESTS.md | тестовый стенд, CI, сборка, документация (T-, B-, D-), инциденты аудита |
| TEST_MATRIX.md | что, каким способом, сколько случаев, PASS/FAIL/BLOCKED; релизные ворота |
| BUILD_AND_PROOFS.md | тулчейны, команды, хеши, воспроизводимость `bin/`, аксиомы, граница доказанного |
| PRODUCTION_GO.md, PRODUCTION_PYTHON.md | независимые production-like хосты и их результаты |
| PERFORMANCE.md | машина, метрики, пределы, soak, ограничения замеров |
| REPRODUCTIONS/ | указатель воспроизведений по находкам + скрипт оркестратора |
| LOGS/orchestrator/ | журналы оркестратора (сборка, CI, verify, FFI, Go) |
| agents/<агент>/ | `REPORT.md`, `LOGS/`, `REPRODUCTIONS/` каждого из 13 агентов |
| hosts/ | общая спецификация хостов, 7 ботов, 15 корпусов, выходы Go и Python |
| PATCHES/ | серии патчей ветки `audit/integration` и bundle со всеми ветками аудита |
| tools/ | бриф агентов, `run.sh` (журналирование команд), скрипты оркестратора |
| FIXES.md | итог волны исправлений: статус каждого пункта P/I/T/B/D, дефекты интеграции, проверки итогового кода, изменения для хостов, остаточные риски |
| fixes/ | брифы волны исправлений, `REPORT.md` и журналы агентов core, protocol, ffi, client, docs, final (интеграция) и release (выпуск) |
| MANIFEST.json | SHA-256 всех файлов каталога, версии, ветки и коммиты |

Агенты: core-compiler, proofs, formats-security (прерван), formats-correctness, engine-timers, ffi-go,
python-client, harness-ci, host-perf (волна 1); integration, cross-verify (волна 2); host-python, host-go (волна 3).

Журналы в `agents/*/LOGS` сделаны `tools/run.sh`: файл `*.log` = команда, каталог, время, код выхода, хвосты
вывода; рядом полные `*.stdout`/`*.stderr`. Пути `/home/user/figram-audit…` внутри журналов — пути среды аудита:
`/home/user/figram-audit/agents/X` ↔ `audit-final/agents/X`, `/home/user/figram-audit/LOGS` ↔ `audit-final/LOGS/orchestrator`,
`/home/user/figram-audit-worktrees/X` — рабочее дерево ветки `audit/X` (восстановить из `PATCHES/audit-branches.bundle`).
Файлы больше 3 МБ сжаты gzip; скомпилированный бинарник `host-go/REPRODUCTIONS/np04/init_race` исключён (собирается из исходников рядом).

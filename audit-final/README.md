# audit-final — итоговый аудит Figram 0.3.0

Начните с **EXECUTIVE.md**. Судьба каждой находки — **FIXES.md**; проверки итогового кода —
**TEST_MATRIX.md**. Второй раунд (перепроверка исправлений и производственные хосты) — в
**round2/**, начните с `round2/EXECUTIVE.md`.

| файл | содержание |
|---|---|
| EXECUTIVE.md | итог, агенты и объём проверок, что изменилось, остаточные риски |
| FIXES.md | находки всех агентов: статус (исправлено / описано / не менялось) и коммит |
| TEST_MATRIX.md | полная локальная проверка коммита 598fec6 (13 шагов, коды выхода, числа) и CI |
| agents/impl-oracle/, agents/impl-protocol/ | волна реализации: независимый оракул и тесты компилятора; схемы, клиенты, тесты протокола |
| agents/formal/, agents/semantics/, agents/protocol/, agents/redteam/ | итоговый аудит: формализация; поведение против описания; протокол и контракт хоста; сила тестов |
| agents/*/REPORT.md | отчёт агента: его итоговое сообщение дословно (писать файлы агентам не разрешалось) |
| LOGS/orchestrator/ | журналы оркестратора: полная проверка, ранние прогоны `verify.sh`, отдельные запуски |
| tools/ | брифы агентов (BRIEF4, BRIEF5), `run.sh` (журналирование команд), скрипт полной проверки |
| PATCHES/audit-branches.bundle | ветки агентов (`audit4/*`, `audit5/*`) относительно master 85bae04 |
| MANIFEST.json | SHA-256 всех файлов каталога, исключённые файлы, версии, коммиты |
| round2/ | второй раунд (2026-11-04): перепроверка исправлений, производственные хосты на Python и Go; свой README, EXECUTIVE, FIXES, TEST_MATRIX, MANIFEST |

Журналы в `agents/*/LOGS` сделаны `tools/run.sh`: файл `*.log` = команда, каталог, время, код
выхода, хвосты вывода; рядом полные `*.stdout`/`*.stderr`. Пути внутри журналов — пути среды
аудита: `/home/user/figram-audit/agents4/X` ↔ `agents/impl-X`, `/home/user/figram-audit/agents5/X`
↔ `agents/X`, `/home/user/figram-audit/cleanup/LOGS/X` ↔ `LOGS/orchestrator/X`,
`/home/user/figram-audit-worktrees/a4-X`, `a5-X` — рабочие деревья веток `audit4/X`, `audit5/X`
(восстановить из `PATCHES/audit-branches.bundle`). Файлы больше 3 МБ сжаты gzip; скомпилированные
бинарники исключены (собираются из исходников и патчей `agents/redteam/DEFECTS/`; их SHA-256 —
в `MANIFEST.json`). Один пункт отчёта `protocol` (P3-11) заменён ссылкой на приватный advisory.

Агенты: impl-oracle, impl-protocol (волна реализации); formal, semantics, protocol, redteam
(итоговый аудит).

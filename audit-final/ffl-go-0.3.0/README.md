# audit-final/ffl-go-0.3.0 — аудит Go-модуля ffl-go 0.3.0

Независимый аудит модуля [`github.com/Ruddy35/ffl-go`](https://github.com/Ruddy35/ffl-go) на
коммите 51c2312 ([Ruddy35/ffl-go#3](https://github.com/Ruddy35/ffl-go/pull/3) до исправлений):
переход на Figram v0.3.0 и протокол 3. Начните с **EXECUTIVE.md**. Судьба каждой находки —
**FIXES.md**; проверки исправленного кода, CI и релиза — **TEST_MATRIX.md**. Аудит самого
движка Figram 0.3.0 — в родительском каталоге `audit-final/` и в `audit-final/round2/`.

| файл | содержание |
|---|---|
| EXECUTIVE.md | итог, объём проверок, что изменилось, остаточные риски |
| FIXES.md | находки агента: статус (исправлено / описано / не менялось) и коммит |
| TEST_MATRIX.md | локальные проверки коммита 361815c (коды выхода, числа), CI, релиз v0.3.0, проверка через прокси Go |
| agents/fflgo/ | материалы агента: генераторы ответов, дифференциальный судья, хост на Go, копии модуля с дефектами, корпуса (gzip), журналы |
| agents/fflgo/REPORT.md | отчёт агента: его итоговое сообщение дословно (писать файлы отчётов агенту не разрешалось) |
| LOGS/orchestrator/ffl-go-030/ | журналы оркестратора: проверки до и после исправлений, проверка через прокси Go, `check_docs` Figram |
| tools/ | бриф агента (BRIEF7), `run.sh` (журналирование команд), проверка модуля через прокси Go (`proxy-consumer/`), мутанты агента на исправленном коде (`agent_mutants_fixed.py`), проба хвоста ответа (`ws-probe/`), `pack_fflgo.py` |
| MANIFEST.json | SHA-256 всех файлов каталога, исключённые файлы, коммиты, запуски CI |

Журналы сделаны `tools/run.sh`: файл `*.log` = команда, каталог, время, код выхода, хвосты
вывода; рядом полные `*.stdout`/`*.stderr`. Пути внутри журналов — пути среды аудита:
`/home/user/figram-audit/agents7/fflgo` ↔ `agents/fflgo`, `/home/user/figram-audit/cleanup/LOGS/ffl-go-030`
↔ `LOGS/orchestrator/ffl-go-030`, `/home/user/ffl-go` — рабочая копия ffl-go (ветка
`claude/kind-ramanujan-n4q8mg`), `/home/user/figram-audit-worktrees/a7-fflgo` — рабочее дерево
ветки агента `audit7/fflgo` (на 51c2312, без своих коммитов: агент не менял код модуля),
`/home/user/figram-audit-worktrees/cleanup-int` — рабочее дерево Figram (только чтение для агента).

Исключены скомпилированные бинарники (`auditclaims`, `audithost`, `refjudge`; собираются
`go build` из исходников рядом) и статические библиотеки `lib/linux_amd64/*.a` в копиях модуля
`agents/fflgo/mod*` (это неизменённые архивы релиза Figram v0.3.0, как в самом модуле; их SHA-256
— в `MANIFEST.json`, сверка с релизом — `scripts/update-lib.sh --check`). Файлы больше 3 МБ сжаты
gzip; корпуса агент сжал сам после прогонов.

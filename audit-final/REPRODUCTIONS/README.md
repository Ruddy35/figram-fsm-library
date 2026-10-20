# Воспроизведения

Каждое воспроизведение запускается одной командой. Скрипты агентов лежат в `../agents/<агент>/REPRODUCTIONS/`
(часть импортирует тестовый код из веток `audit/<агент>` — восстановите их из `../PATCHES/audit-branches.bundle`:
`git fetch ../PATCHES/audit-branches.bundle 'refs/heads/audit/*:refs/heads/audit/*'`).
`ENGINE` — `bin/figram-engine` (или свежая сборка), `FIGRAM` — `bin/figram`.

| находка | команда | «дефект воспроизведён» |
|---|---|---|
| P-01 (ключи после `start`) | `python3 -I REPRODUCTIONS/orchestrator/repro_et01.py bin/figram-engine examples/pizza.bot.json` | код 1, «create_order executed in CRM: 1 (expected 2)» |
| P-01 | `python3 agents/engine-timers/REPRODUCTIONS/repro.py f1-start-keys` | VERDICT BUG |
| P-02 (условия до outbox) | `bash agents/host-perf/REPRODUCTIONS/host_h1_guard_before_call.sh`; `python3 agents/cross-verify/REPRODUCTIONS/c1_guard_before_outbox.py ENGINE examples/pizza.bot.json SCRATCH`* | catalog вместо address; 2 товара |
| P-03 (getUpdates) | `bash agents/host-perf/REPRODUCTIONS/host_h4_getupdates_ack.sh`; `python3 agents/cross-verify/REPRODUCTIONS/c2_cumulative_ack.py ENGINE examples/pizza.bot.json`* | заказов 0 |
| P-04 (таймер старой версии) | `python3 agents/core-compiler/REPRODUCTIONS/H2_stale_timer_after_upgrade/repro.py`; `sh agents/core-compiler/REPRODUCTIONS/H2_min_shrunk/repro.sh` | код 1, `call k1` на D |
| P-05 (устаревший снимок) | `python3 agents/engine-timers/REPRODUCTIONS/repro.py f6-stale-restart`; `bash agents/host-perf/REPRODUCTIONS/host_h2_seq_keys.sh` | коллизии ключей / 2 заказа |
| P-06 (загрузка) | `sh agents/core-compiler/REPRODUCTIONS/PERF_compile_cliff/repro.sh`; `bash agents/host-perf/REPRODUCTIONS/perf_series.sh unique` | десятки–сотни секунд |
| P-08 (гонка `figram_init`) | `bash agents/ffi-go/REPRODUCTIONS/repro_c_baseline.sh`; `agents/host-go/REPRODUCTIONS/np04/run.sh 300 concurrent` | SIGSEGV (rc 139) в части запусков |
| P-09 (RLIMIT_AS) | `bash agents/host-go/REPRODUCTIONS/np01_rlimit_as.sh`; `python3 agents/engine-timers/REPRODUCTIONS/repro.py f11-rlimit` | SIGABRT / rc −6 |
| P-12 (`fmt`) | `bash agents/formats-correctness/REPRODUCTIONS/fc01_exponent_roundtrip.sh`; `bash agents/proofs/REPRODUCTIONS/fmt_exponent.sh` | `check(fmt x)` → код 1 |
| P-13 (вывод CLI) | `bash agents/formats-correctness/REPRODUCTIONS/fc06_cli_injection.sh` | строка `ok` при коде 1 |
| P-14 (лишние `need`) | `sh agents/core-compiler/REPRODUCTIONS/H3_need_superfluous/repro.sh` | need c1, результат одинаков |
| I-01 (клиент и долгая загрузка) | `bash agents/host-perf/REPRODUCTIONS/perf_client_timeout.sh`; `python3 agents/cross-verify/REPRODUCTIONS/c3_slow_load_client.py ENGINE SCRATCH/unique-800.bot.json`* | EngineError каждые 10 с |
| I-03 (`id:null` → общий процесс) | `python3 agents/python-client/REPRODUCTIONS/r10_id_null_poison.py` | код 1, ошибки у соседей |
| T-01 (ложнозелёный verify) | `bash agents/harness-ci/REPRODUCTIONS/r01_andlist_extract.sh`; `bash agents/harness-ci/REPRODUCTIONS/r03_golden_truncated_green.sh --quick`; реальный прогон: `LOGS/orchestrator/38-verify-sh-adapted-fresh.log` | «ВСЁ В ПОРЯДКЕ», код 0 при расхождении |
| T-03 (шлюз аксиом) | `VERIFY_REV=d69bbaf bash agents/proofs/REPRODUCTIONS/axiom_gate_canary.sh` | старый шлюз пропускает axiom/native_decide/addDeclCore |
| T-04 (протокольные мутанты) | `agents/harness-ci/REPRODUCTIONS/r10_engine_mutants/` (матрица `agents/harness-ci/LOGS/r10/matrix.tsv`) | 6 из 20 выживают на baseline |

\* скрипты cross-verify импортируют `../ffi/python-example`: запускайте их копию `xverify/` из корня дерева ветки `audit/cross-verify`.

Полный перечень — в разделах «Повтор» отчётов агентов (`agents/*/REPORT.md`).

Воспроизведения агента host-perf. Каждый скрипт запускается одной командой;
первый аргумент (необязательный) — путь к figram-engine, по умолчанию
bin/figram-engine базового коммита (CI-verified = официальная свежая сборка,
/home/user/figram-audit/LOGS/10-ci-verify-run-39330382243.log). Код сценариев закоммичен
в ветке audit/host-perf: /home/user/figram-audit-worktrees/host-perf/audit/host-perf/.

  host_h1_guard_before_call.sh   H1: условия до исполнения call из outbox (h1a, h1b, h1c)
  host_h2_seq_keys.sh            H2: ключи user:seq:index после start / отката базы (h2a–h2d)
  host_h3_dedup_executor.sh      H3: рост отсева, таймеры, порядок/отказы исполнителя (h3a–h3f)
  host_h4_getupdates_ack.sh      H4: накопительное подтверждение getUpdates
  host_h5_event_payload.sh       H5: условие/действие по данным события
  host_sim_mutants.sh [E] base|fixed  сила host_sim.py (базового / исправленного): выжившие мутации
  perf_pizza.sh                  задержка/пропускная способность на pizza (канал и прогон файла без Python)
  perf_series.sh SERIES          масштабируемость (states|unique|unique_big|global|global_big|branches|depth|resume|random|readme)
  perf_soak.sh [SECONDS]         длительный прогон: дрейф RSS
  perf_client_timeout.sh         клиент Python (таймаут 10 с) на документе из 3000 сообщений
  host_sim_regression.sh         исправленный host_sim: без барьера красный, с барьером зелёный
  perf_soak_negative.sh          отрицательный контроль soak: «текущий» прокси-движок → большой наклон RSS

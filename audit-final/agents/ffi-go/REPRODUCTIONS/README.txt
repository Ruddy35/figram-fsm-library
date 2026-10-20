ffi-go: воспроизведения (каждое — одна команда; нужны `source /home/user/figram-audit/env.sh`
и собранный ffi/audit/out в /home/user/figram-audit-worktrees/ffi-go: `ffi/audit/build_audit.sh`).

repro_c_baseline.sh   — FFI-01, 03, 05, 06, 07, 08, 09, 10 на baseline-прослойке d69bbaf (лог LOGS/60-repro-c-baseline.log)
repro_go_baseline.sh  — FFI-01 (Go), FFI-08 (Go), FFI-11, FFI-12 на baseline main.go + figram_ffi.c (LOGS/61-repro-go-baseline.log)
repro_nomt_control.sh — FFI-15: мутант без lean_mark_mt — стресс красный, TSan молчит (LOGS/62-repro-nomt-control.log)
go_stale_cgo_lib_NOT_REPRODUCED.sh — гипотеза об устаревшей libfigram.a в кэше Go НЕ воспроизвелась (LOGS/46-*)

Регрессия после исправлений (ветка audit/ffi-go): ffi/audit/regress.sh (LOGS/40 до, 42 после),
Go: go test -race . в ffi/go-example (LOGS/24 до, 47 после), ffi/audit/go_verify_block.sh (LOGS/49).

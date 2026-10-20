host-go reproductions (each one command; run build.sh first):
  build.sh                         build host/runner/probes against the real libfigram.a
  np01_rlimit_as.sh                NP-01 figram_init aborts the host under RLIMIT_AS=3GB
  np02_embed_side_effects.sh       NP-02 threads/VSZ/sigaltstack side effects of figram_init
  np03_c_strings.sh                NP-03 NUL / invalid UTF-8 / empty line through the C ABI
  np04/run.sh 300 concurrent       NP-04 8 threads call figram_init at once -> SIGSEGV (control: "300 serial")
  np05_strict_snapshot_timers.sh   NP-05 strict snapshot.timers validation poison-loops after upgrade
  red_ack_retry.jsonl              targeted corpus: DB locked between Telegram send and "done" mark
  all corpora:   audit/host-go/scripts/run_all.sh            (outputs -> hosts/out/go)
  RED matrix:    python3 -I audit/host-go/scripts/red_matrix.py OUT.json SCRATCH
  Go vs Python:  python3 -I audit/host-go/compare/compare.py hosts/out/go hosts/out/python hosts/shared/corpus
  soak:          bin/fgrun soak -secs 660 -users 12 -think 20ms -reload-every 5s -out soak.json

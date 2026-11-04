#!/usr/bin/env bash
# The remaining measurements, one heavy job at a time.
set -u
O=/home/user/figram-audit/agents6/hostgo
H=$O/host/bin/hostgo
R=/home/user/figram-audit/run.sh
for p in naive shim-errors stale closed-window unload-wrapper unload-raw-wait; do
  $R $O/LOGS/abi-$p nice -n 10 timeout 300 $H abi $p
done
for i in 1 2 3; do  # undefined behaviour: may crash; three attempts of 10 s each
  $R $O/LOGS/abi-unload-raw-$i nice -n 10 timeout 120 $H abi unload-raw 10s
done
$R $O/LOGS/bench nice -n 10 timeout 900 $H bench
bash $O/scripts/green.sh race
BIG=1000 bash $O/scripts/green.sh big
$R $O/LOGS/soak nice -n 10 timeout 900 $H soak 10m $O/results/soak.json

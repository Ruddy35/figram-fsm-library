#!/usr/bin/env bash
# fix/final: mutation analysis of figram_client.py (fix-client's mutate_client_v2.py) against the
# final engine of fix/final, in batches (each < 30 min), sequentially.
set -u
W=/home/user/figram-audit-worktrees/fix-final
L=/home/user/figram-audit/fixes/final/LOGS/client-mutants
E=/home/user/figram-audit/fixes/final/bin-final/figram-engine.stripped
M=/home/user/figram-audit/fixes/client/mutate_client_v2.py
mkdir -p $L
cd $W
BATCHES=(
  M01,M02,M03,M04,M05,M06
  M07,M08,M09,M10,M11,M12
  M13,M14,M15,M16,M17,M18
  M18b,M19,M20,M21,M22,M23
  M24,M25,M26,M27,M28,M29
  M30,M31,M32,M32b,M33,M34
  M35,M36,M37,M38,M39,M40
  M41,M42,M43,M44,M45,M46,M47
)
i=0
for B in "${BATCHES[@]}"; do
  i=$((i+1))
  skip=""; [ $i -gt 1 ] && skip=1
  FAST=1 ONLY=$B SKIP_BASE=$skip nice -n 10 timeout 1790 /home/user/figram-audit/run.sh $L/B$i \
    python3 $M ffi/python-example $E examples/pizza.bot.json examples/features.bot.json
done
echo ALL-BATCHES-DONE

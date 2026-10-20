#!/usr/bin/env bash
# cross-verify: run every independent reproduction on one engine pair.
# Usage: xverify/run_all.sh FIGRAM FIGRAM_ENGINE SCRATCH   (C9 needs the lean toolchain, see c9 script)
set -u
D=$(cd "$(dirname "$0")/.." && pwd); F=$1; E=$2; S=$3; mkdir -p "$S"
cd "$D"
python3 xverify/c1_guard_before_outbox.py "$E" examples/pizza.bot.json "$S/c1"; echo "C1 rc=$?"
python3 xverify/c2_cumulative_ack.py "$E" examples/pizza.bot.json; echo "C2 rc=$?"
python3 -c "import sys,json; sys.path.insert(0,'xverify'); import c6_load_perf as c; json.dump(c.doc('unique',800), open('$S/unique-800.bot.json','w'))"
python3 xverify/c3_slow_load_client.py "$E" "$S/unique-800.bot.json"; echo "C3 rc=$?"
python3 xverify/c4_c5_shared_process.py "$E" examples/pizza.bot.json control; echo "control rc=$?"
python3 xverify/c4_c5_shared_process.py "$E" examples/pizza.bot.json c4 60000 10 2; echo "C4 rc=$?"
python3 xverify/c4_c5_shared_process.py "$E" examples/pizza.bot.json c5; echo "C5 rc=$?"
python3 xverify/c4_c5_shared_process.py "$E" examples/pizza.bot.json c5semantic; echo "C5 semantic-control rc=$?"
python3 xverify/c4_c5_shared_process.py "$E" examples/pizza.bot.json c5 0 10 2 8 0.01; echo "C5 low-concurrency rc=$? (0 or 1: only in-flight requests fail)"
nice -n 10 python3 xverify/c6_load_perf.py "$F" "$E" "$S/c6" common 500 1000 2000 --cap 300; echo "C6a rc=$?"
nice -n 10 python3 xverify/c6_load_perf.py "$F" "$E" "$S/c6" unique 125 250 500 --cap 300; echo "C6b rc=$?"
python3 xverify/c7_outbox_executors.py "$E" examples/features.bot.json examples/pizza.bot.json; echo "C7 rc=$?"
bash xverify/c8_check_injection.sh "$F" "$E" "$S/c8"; echo "C8 rc=$?"

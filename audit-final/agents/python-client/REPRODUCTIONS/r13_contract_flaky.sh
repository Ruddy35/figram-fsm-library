#!/bin/sh
# PC-14: client_contract.py нестабилен: state() ловит только FileNotFoundError,
# а чтение /proc/PID/stat собираемого в этот момент процесса даёт ESRCH
# (ProcessLookupError). Прогон N раз, печать числа отказов с этой трассировкой.
#   sh r13_contract_flaky.sh CLIENT_DIR ENGINE FEATURES [N]
d=$1; e=$2; f=$3; n=${4:-20}; fails=0; esrch=0
for i in $(seq 1 $n); do
  out=$(cd "$d" && python3 client_contract.py "$e" "$f" 2>&1); rc=$?
  [ $rc -ne 0 ] && fails=$((fails+1))
  echo "$out" | grep -q ProcessLookupError && esrch=$((esrch+1))
done
echo "прогонов $n: отказов $fails, из них ProcessLookupError $esrch"
[ $fails -eq 0 ]

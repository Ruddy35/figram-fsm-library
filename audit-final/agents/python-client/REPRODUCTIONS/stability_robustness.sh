#!/bin/sh
# Повторные прогоны client_robustness.py для проверки стабильности.
#   sh stability_robustness.sh WORKTREE N
W=$1; n=${2:-5}; fails=0
for i in $(seq 1 $n); do
  (cd "$W" && python3 ffi/python-example/client_robustness.py bin/figram-engine examples/pizza.bot.json examples/features.bot.json) > /dev/null 2>&1 || fails=$((fails+1))
done
echo "прогонов $n, отказов $fails"
[ $fails -eq 0 ]

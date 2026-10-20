#!/bin/sh
# host_sim_repeat.sh DIR ENGINE N [args]: run host_sim.py N times, count exit codes, main-scenario and control verdicts
D=$1; E=$2; N=$3; shift 3
cd "$D" || exit 2
ok=0; bad=0; ctlmiss=0; mainbad=0
i=0; while [ $i -lt $N ]; do i=$((i+1))
  out=$(nice -n 10 timeout 300 python3 host_sim.py "$E" ../../examples/pizza.bot.json "$@" 2>&1); rc=$?
  [ $rc -eq 0 ] && ok=$((ok+1)) || bad=$((bad+1))
  echo "$out" | grep -q "тест ловит нарушение: НЕТ" && ctlmiss=$((ctlmiss+1))
  echo "$out" | sed -n '1,4p' | grep -qE "не так, как ожидалось|ровно по одному на пользователя: НЕТ|потерянных обновлений [1-9]" && mainbad=$((mainbad+1))
  echo "run $i rc=$rc"
done
echo "runs=$N exit0=$ok exit1=$bad control_not_red=$ctlmiss main_scenario_red=$mainbad"

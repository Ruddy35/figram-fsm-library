#!/usr/bin/env bash
# figram-engine в memory-cgroup с лимитом 64 МБ (как лимит памяти контейнера), 20000 запросов.
cd /home/user/figram-fsm-library
C=/sys/fs/cgroup/memory/figram-test; mkdir -p $C; echo $((64<<20)) > $C/memory.limit_in_bytes
sh -c "echo \$\$ > $C/cgroup.procs; exec python3 -I /home/user/figram-audit/fixes/release/cgroup_load.py"
rc=$?
echo "пик памяти группы (python + движок): $(( $(cat $C/memory.max_usage_in_bytes) >> 20 )) МБ при лимите 64 МБ; отказов лимита: $(cat $C/memory.failcnt)"
rmdir $C
exit $rc

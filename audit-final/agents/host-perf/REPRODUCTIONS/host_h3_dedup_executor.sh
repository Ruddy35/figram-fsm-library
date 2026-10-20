#!/usr/bin/env bash
# Сценарии h3a,h3b,h3c,h3d,h3e,h3f модели хоста (HOST.md буквально и исправленный вариант).
# Код 0 — все контрпримеры воспроизведены, исправленный хост их не даёт.
E=${1:-/home/user/figram-audit-worktrees/host-perf/bin/figram-engine}
exec nice -n 10 timeout 600 python3 /home/user/figram-audit-worktrees/host-perf/audit/host-perf/host_model/host_model.py "$E" --only h3a,h3b,h3c,h3d,h3e,h3f

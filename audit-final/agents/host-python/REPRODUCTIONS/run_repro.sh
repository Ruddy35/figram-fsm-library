#!/usr/bin/env bash
# usage: run_repro.sh <repro.jsonl> [broken-variant]   -> prints the runner summary (fails = RED invariants)
set -u
H=/home/user/figram-audit-worktrees/host-python/audit/host-python
R=$(cd "$(dirname "$0")" && pwd)
c="$1"; b="${2:-}"; n=$(basename "$c" .jsonl)
out="${TMPDIR:-/tmp}/hostpy-repro-$n-${b:-ok}.json"
HOST_WORK="${TMPDIR:-/tmp}/hostpy-repro-work" nice -n 10 timeout 300 python3 -I "$H/runner.py" "$R/$c" --out "$out" \
  --engine /home/user/figram-audit-worktrees/host-python/bin/figram-engine --bots /home/user/figram-audit/hosts/shared/bots \
  ${b:+--broken "$b"} --no-ref
rc=$?
python3 - "$out" <<'PY'
import json,sys
d=json.load(open(sys.argv[1]))
for u,v in d["users"].items():
    print(u, "final:", v["snapshot"])
    for t in v["transitions"]:
        print("  ", t["update_id"], t["event"], "tok=%s"%t["token"] if t["token"] is not None else "", "seq", t["seq"], "->", t["at"], "t_ms", t["t_ms"], t["actions"])
print("effects:", [(e["key"], e.get("call") or e.get("send")) for e in d["effects"] if "call" in e])
print("FAILED invariants:", json.dumps(d["invariant_failures"], ensure_ascii=False)[:1500])
PY
exit $rc

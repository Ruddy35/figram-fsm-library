#!/usr/bin/env bash
# NP-05: after an upgrade the engine keeps timers of the OLD document in snapshot.timers
# (ET-02). A host whose reply validator requires snapshot.timers to be timers of the
# current document (natural reading of FORMAT §7) poison-loops: updates abandoned.
W=/home/user/figram-audit-worktrees/host-go/audit/host-go
"$W/bin/fgrun" run /home/user/figram-audit/hosts/shared/corpus/migration-1.jsonl -out /tmp/np05.json -quiet \
   -no-ref -broken strict-snapshot-timers -max-nacks 20 | head -c 600; echo
python3 -c "import json; d=json.load(open('/tmp/np05.json')); print(d['invariant_failures'].get('all_updates_acked', [])[:3])"

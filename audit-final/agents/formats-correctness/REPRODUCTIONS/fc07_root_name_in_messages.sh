#!/usr/bin/env bash
# FC-07: the root section is called «» in lint messages but «bot» in structural messages;
# an empty child name collides with the internal root id "".
F="${1:-/home/user/figram-audit-worktrees/formats-correctness/bin/figram}"
d=$(mktemp -d); trap 'rm -rf "$d"' EXIT
printf '{"figram":1,"bot":{"initial":"b","on":{"":[]},"children":{"a":{},"":{}}}}' > "$d/r.json"
"$F" check "$d/r.json"; echo "rc=$?"

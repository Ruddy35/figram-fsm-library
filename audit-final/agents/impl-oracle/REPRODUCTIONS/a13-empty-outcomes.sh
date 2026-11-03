#!/bin/sh
# A13 (prose ambiguity, docs/format.md section 3 decision 11, section 4 `Call`): `"outcomes": []`.
# Decision 11 lists the accepted explicit defaults (`{"text": …}`, `"detached": false`,
# `"every": false`) but not `"outcomes": []`; section 3 requires outcome NAMES to be non-empty, not
# the list. figram accepts `[]` as "no outcomes" (the call carries no token) and then rejects
# `"every": true` next to it ("`every` needs `outcomes`").
F=${1:-/home/user/figram-audit-worktrees/a4-oracle/.lake/build/bin/figram}
E=${2:-/home/user/figram-audit-worktrees/a4-oracle/.lake/build/bin/figram-engine}
D=$(mktemp -d)
echo '{"figram":1,"calls":{"a":{"text":"x","outcomes":[]}},"bot":{"initial":"m","children":{"m":{"on":{"btn:x":[{"do":["a","a"]}]}}}}}' > "$D/1.json"
echo '{"figram":1,"calls":{"a":{"text":"x","outcomes":[],"every":true}},"bot":{"initial":"m","children":{"m":{}}}}' > "$D/2.json"
"$F" check "$D/1.json"; echo "rc=$?   (accepted; V6 does not apply: no outcomes)"
"$F" check "$D/2.json"; echo "rc=$?   (rejected: every needs outcomes)"
printf '%s\n' '{"id":1,"op":"step","snapshot":{"at":"m","seq":1},"event":"btn:x"}' | "$E" "$D/1.json"
# engine: {"id":1,"snapshot":{"at":"m","seq":2},"actions":[{"call":"a"},{"call":"a"}]}

#!/bin/sh
# A11 (prose ambiguity, docs/format.md section 7 decision 12 / section 8 rule 2):
# a snapshot that holds the same wait (call, token) twice. Prose: "accepting it removes the
# wait, so each wait accepts one outcome" -> literal reading: one copy removed, the second
# delivery is accepted too. Engine: every copy is removed by the first delivery.
E=${1:-/home/user/figram-audit-worktrees/a4-oracle/.lake/build/bin/figram-engine}
DOC=$(mktemp); cat > "$DOC" <<'J'
{"figram":1,"calls":{"chk":{"text":"check","outcomes":["yes"]}},"bot":{"initial":"a","children":{"a":{}}}}
J
printf '%s\n' \
 '{"id":1,"op":"step","snapshot":{"at":"a","pending":[{"call":"chk","token":3},{"call":"chk","token":3}],"seq":5},"event":"call:chk:yes","token":3}' \
 | "$E" "$DOC"
# engine:          {"id":1,"snapshot":{"at":"a","seq":5},"actions":[]}
# literal reading: {"id":1,"snapshot":{"at":"a","pending":[{"call":"chk","token":3}],"seq":5},"actions":[]}
# A15 (same family): a forged wait with a token not below seq; a new call of an `every` action with
# that token. Engine: the old identical wait is dropped and the new one is put in front ("pending" acts
# as a set of (action, token)); literal "each call has its own wait": both kept.
DOC2=$(mktemp); cat > "$DOC2" <<'J'
{"figram":1,"calls":{"p":{"text":"p","outcomes":["ok"],"every":true}},"bot":{"initial":"a","children":{"a":{"on":{"btn:p":[{"do":["p"]}]}}}}}
J
printf '%s\n' '{"id":2,"op":"step","snapshot":{"at":"a","pending":[{"call":"x","token":1},{"call":"p","token":3}],"seq":2},"event":"btn:p"}' | "$E" "$DOC2"
# engine:  {"id":2,"snapshot":{"at":"a","pending":[{"call":"p","token":3},{"call":"x","token":1}],"seq":3},"actions":[{"call":"p","token":3}]}
# literal: ... "pending":[{"call":"p","token":3},{"call":"x","token":1},{"call":"p","token":3}] ...

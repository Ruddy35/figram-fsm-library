#!/usr/bin/env bash
# FC-05: JSON Schema validators on binary floats (Python jsonschema here; ajv in JS behaves alike)
# accept values the exact decoder rejects, contrary to "движок принимает ровно то, что разрешает
# snapshot.schema.json" (FORMAT.md §7).
W=/home/user/figram-audit-worktrees/formats-correctness
E="${1:-$W/bin/figram-engine}"; F="${2:-$W/bin/figram}"
python3 - "$W" <<'PY'
import json, sys, jsonschema
w = sys.argv[1]
snap = jsonschema.Draft202012Validator(json.load(open(w + "/snapshot.schema.json")))
doc = jsonschema.Draft202012Validator(json.load(open(w + "/figram.schema.json")))
for t in ['{"at":"home","seq":1e-400}', '{"at":"home","seq":9007199254740990.5}', '{"v":1.0000000000000001,"at":"home"}']:
    print("snapshot schema", snap.is_valid(json.loads(t)), t)
for t in ['{"figram":1.0000000000000001,"bot":{"initial":"a","children":{"a":{}}}}',
          '{"figram":1,"bot":{"initial":"a","children":{"a":{"timers":{"t":1e-400}}}}}']:
    print("document schema", doc.is_valid(json.loads(t)), t)
PY
printf '%s\n' '{"id":1,"op":"step","event":"x","snapshot":{"at":"home","seq":1e-400}}' \
  '{"id":2,"op":"step","event":"x","snapshot":{"at":"home","seq":9007199254740990.5}}' \
  '{"id":3,"op":"step","event":"x","snapshot":{"v":1.0000000000000001,"at":"home"}}' | "$E" "$W/examples/features.bot.json"
d=$(mktemp -d); trap 'rm -rf "$d"' EXIT
printf '{"figram":1,"bot":{"initial":"a","children":{"a":{"timers":{"t":1e-400}}}}}' > "$d/t.json"; "$F" check "$d/t.json"; echo "rc=$?"

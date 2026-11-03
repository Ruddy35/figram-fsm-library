# 10: `start` accepts a `call:` event (Engine.decStartEvent)
EDITS = [("Figram/Engine.lean",
"    else if e.startsWith \"call:\" then throw \"an outcome event cannot start a user\"\n",
"")]

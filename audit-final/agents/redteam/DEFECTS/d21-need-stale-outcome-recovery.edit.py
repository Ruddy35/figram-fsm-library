# 21: in recovery `need` asks for the conditions of a stale outcome (Engine.missing)
EDITS = [("Figram/Engine.lean",
"    | .outcome .. => if s.accepts ev then missingStart m ev.key gs else []",
"    | .outcome .. => missingStart m ev.key gs")]

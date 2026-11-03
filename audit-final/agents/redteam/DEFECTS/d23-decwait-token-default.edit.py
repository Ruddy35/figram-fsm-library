# 23: a `pending` entry without `token` decodes with token 0 (Engine.decWait)
EDITS = [("Figram/Engine.lean",
"      | none => throw \"snapshot: a `pending` entry needs `token`\"",
"      | none => pure 0")]

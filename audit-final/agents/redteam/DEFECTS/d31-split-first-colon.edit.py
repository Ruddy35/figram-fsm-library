# 31: the engine splits `call:<action>:<outcome>` at the first `:` (Engine.decEvent)
EDITS = [("Figram/Engine.lean",
"    match splitOutcome (name.drop 5).toString with\n    | none => throw \"an outcome event",
"    match (match (name.drop 5).toString.splitOn \":\" with\n      | c :: o :: rest => if c.isEmpty || o.isEmpty then none else some (c, \":\".intercalate (o :: rest))\n      | _ => none) with\n    | none => throw \"an outcome event")]

# 15c: check rejects an arrow on `failed` (it forgets the implicit outcome)
EDITS = [("Figram/Format.lean",
"    | some c => if c.outcomes.isEmpty then none else some (\"failed\" :: c.outcomes)",
"    | some c => if c.outcomes.isEmpty then none else some c.outcomes")]

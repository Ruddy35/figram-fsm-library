# 15b: check accepts an arrow on an undeclared outcome of an action with outcomes
EDITS = [("Figram/Format.lean",
"        if (outcomesOf n).any (·.contains o) then none",
"        if (outcomesOf n).isSome || o.isEmpty then none")]

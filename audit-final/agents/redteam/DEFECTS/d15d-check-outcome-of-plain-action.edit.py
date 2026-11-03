# 15d: check accepts an outcome arrow for an action without outcomes (any declared action)
EDITS = [("Figram/Format.lean",
"        if (outcomesOf n).any (·.contains o) then none",
"        if (outcomesOf n).any (·.contains o) || d.calls.any (·.1 == n) then none")]

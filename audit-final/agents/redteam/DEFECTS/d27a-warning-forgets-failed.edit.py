# 27a: the warning "outcome ... has no arrow" forgets `failed` (Warnings)
EDITS = [("Figram/Warnings.lean",
"            let os := ((lookup d.calls n).map (·.outcomes)).getD [] ++ [\"failed\"]",
"            let os := ((lookup d.calls n).map (·.outcomes)).getD []")]

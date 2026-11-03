# 4: `every` replaces (behaves as `latest`) (shared addWait)
EDITS = [("Figram/Core.lean",
"  | .every => (n, tok) :: pend.filter (fun p => decide (p ≠ (n, tok)))",
"  | .every => assign pend n tok")]

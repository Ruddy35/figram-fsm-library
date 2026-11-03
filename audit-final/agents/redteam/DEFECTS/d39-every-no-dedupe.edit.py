# 39: `every` does not drop an equal wait before adding its own (shared addWait)
EDITS = [("Figram/Core.lean",
"  | .every => (n, tok) :: pend.filter (fun p => decide (p ≠ (n, tok)))",
"  | .every => (n, tok) :: pend")]

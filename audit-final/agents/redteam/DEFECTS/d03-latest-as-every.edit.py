# 3: `latest` keeps the older waits (behaves as `every`) (shared addWait)
EDITS = [("Figram/Core.lean",
"  | .latest => assign pend n tok",
"  | .latest => (n, tok) :: pend.filter (fun p => decide (p ≠ (n, tok)))")]

# 14b: toChart ignores `detached` (Format.callOpts)
EDITS = [("Figram/Format.lean",
"  | some c => (c.detached, c.wait)",
"  | some c => (c.detached && false, c.wait)")]

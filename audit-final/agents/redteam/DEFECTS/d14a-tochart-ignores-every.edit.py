# 14a: toChart ignores `every` (Format.CallDecl.wait)
EDITS = [("Figram/Format.lean",
"  if c.outcomes.isEmpty then .free else if c.every then .every else .latest",
"  if c.outcomes.isEmpty then .free else if c.every && false then .every else .latest")]

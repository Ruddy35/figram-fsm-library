# 28: a call with only `detached` is printed as a plain text (Format.CallDecl.plain): `fmt` loses it
EDITS = [("Figram/Format.lean",
"  !c.detached && c.outcomes.isEmpty && !c.every && c.ext.isEmpty",
"  c.outcomes.isEmpty && !c.every && c.ext.isEmpty")]

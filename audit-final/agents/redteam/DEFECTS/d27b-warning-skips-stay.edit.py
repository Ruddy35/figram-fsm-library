# 27b: the warning "outcome ... has no arrow" does not check variants without a target (Warnings)
EDITS = [("Figram/Warnings.lean",
"              | .stay => c.leafIds.filter fun l => (ix.anc l).contains t.src",
"              | .stay => c.leafIds.filter fun l => (ix.anc l).contains t.src && false")]

# 1b: an accepted outcome whose arrow has only false conditions does not remove its wait
EDITS = [("Figram/Core.lean",
"      match r.branches.find? (fun b => guardOK env b.guard) with\n      | none => (s.settle ev, [])",
"      match r.branches.find? (fun b => guardOK env b.guard) with\n      | none => (s, [])")]

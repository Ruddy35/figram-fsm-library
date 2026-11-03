# 25: an outcome handled by a variant without a target keeps its wait (Machine.step, stay path)
EDITS = [("Figram/Core.lean",
"      | some b => if b.target = .stay then stay (s.settle ev) ev b.actions else m.fire (s.settle ev) b",
"      | some b => if b.target = .stay then stay s ev b.actions else m.fire (s.settle ev) b")]

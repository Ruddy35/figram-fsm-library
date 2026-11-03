# 5: a variant without a target re-enters the message (exit + entry) (Machine.step only)
EDITS = [("Figram/Core.lean",
"      | some b => if b.target = .stay then stay (s.settle ev) ev b.actions else m.fire (s.settle ev) b",
"      | some b => m.fire (s.settle ev) b")]

# 6b: a stay timer firing does not consume the timer (Machine.step only)
EDITS = [("Figram/Core.lean",
"      | some b => if b.target = .stay then stay (s.settle ev) ev b.actions else m.fire (s.settle ev) b",
"      | some b => if b.target = .stay then realize (s.settle ev) s.state b.actions else m.fire (s.settle ev) b")]

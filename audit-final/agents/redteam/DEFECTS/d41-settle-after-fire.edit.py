# 41 (expected equivalent): an accepted outcome removes its wait after the move instead of before (Machine.step)
EDITS = [("Figram/Core.lean",
"      | some b => if b.target = .stay then stay (s.settle ev) ev b.actions else m.fire (s.settle ev) b",
"      | some b => if b.target = .stay then stay (s.settle ev) ev b.actions else\n        let r := m.fire s b\n        (r.1.settle ev, r.2)")]

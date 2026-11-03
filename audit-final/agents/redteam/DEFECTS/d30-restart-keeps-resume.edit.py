# 30 (control, not 0.3.0): `restart` keeps the remembered places
EDITS = [("Figram/Engine.lean",
"    let (s', as) := m.recover { s with resume := [], back := none, pending := [] }",
"    let (s', as) := m.recover { s with back := none, pending := [] }")]

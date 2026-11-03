# 9: `restart` keeps `pending` (Engine.handleObj)
EDITS = [("Figram/Engine.lean",
"    let (s', as) := m.recover { s with resume := [], back := none, pending := [] }",
"    let (s', as) := m.recover { s with resume := [], back := none }")]

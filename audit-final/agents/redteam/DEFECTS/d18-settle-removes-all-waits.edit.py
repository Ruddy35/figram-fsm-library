# 18: an outcome removes every wait of its action, not only the one with its token (shared settle)
EDITS = [("Figram/Core.lean",
"  | .outcome c _ tok => { s with pending := s.pending.filter (fun p => decide (p ≠ (c, tok))) }",
"  | .outcome c _ _ => { s with pending := s.pending.filter (fun p => p.1 != c) }")]

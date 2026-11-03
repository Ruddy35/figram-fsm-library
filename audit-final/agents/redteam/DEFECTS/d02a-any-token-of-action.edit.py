# 2a: an outcome is accepted with any token of a wait of its action (shared Snapshot.accepts)
EDITS = [("Figram/Core.lean",
"  | .outcome c _ tok => decide ((c, tok) ∈ s.pending)",
"  | .outcome c _ _ => s.pending.any (·.1 == c)")]

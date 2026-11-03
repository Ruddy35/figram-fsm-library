# 2b: an outcome is accepted with any token at all (shared Snapshot.accepts)
EDITS = [("Figram/Core.lean",
"  | .outcome c _ tok => decide ((c, tok) ∈ s.pending)",
"  | .outcome _ _ _ => true")]

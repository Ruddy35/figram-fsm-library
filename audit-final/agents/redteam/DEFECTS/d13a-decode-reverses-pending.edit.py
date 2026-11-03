# 13a: the snapshot decoder reverses `pending` (Engine.decSnapshot)
EDITS = [("Figram/Engine.lean",
"    pure ⟨st, res, tms, seq, bk, pend⟩",
"    pure ⟨st, res, tms, seq, bk, pend.reverse⟩")]

# 13d: the snapshot decoder sorts `pending` by action name (Engine.decSnapshot)
EDITS = [("Figram/Engine.lean",
"    pure ⟨st, res, tms, seq, bk, pend⟩",
"    pure ⟨st, res, tms, seq, bk, pend.mergeSort (fun a b => decide (a.1 < b.1) || a.1 == b.1)⟩")]

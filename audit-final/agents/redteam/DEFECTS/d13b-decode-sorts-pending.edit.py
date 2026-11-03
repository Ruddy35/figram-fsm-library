# 13b: the snapshot decoder sorts `pending` by token, oldest first (Engine.decSnapshot)
EDITS = [("Figram/Engine.lean",
"    pure ⟨st, res, tms, seq, bk, pend⟩",
"    pure ⟨st, res, tms, seq, bk, pend.mergeSort (fun a b => decide (a.2 ≤ b.2))⟩")]

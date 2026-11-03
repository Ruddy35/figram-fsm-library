# 13c: the snapshot encoder writes `pending` oldest first (Engine.encSnapshot, shared with figram trace)
EDITS = [("Figram/Engine.lean",
"        (.arr (s.pending.map fun p =>",
"        (.arr (s.pending.reverse.map fun p =>")]

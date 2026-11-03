# 11b: the engine encoder does not write the `token` of `call` (Engine.encAction, shared with figram trace)
EDITS = [("Figram/Engine.lean",
"field \"token\" (w != .free) (.num tok 0)",
"field \"token\" (w != .free && false) (.num tok 0)")]

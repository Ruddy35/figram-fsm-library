# 48: the engine encoder writes a `token` on every call, also without outcomes (Engine.encAction)
EDITS = [("Figram/Engine.lean",
"field \"token\" (w != .free) (.num tok 0)",
"field \"token\" (w != .free || true) (.num tok 0)")]

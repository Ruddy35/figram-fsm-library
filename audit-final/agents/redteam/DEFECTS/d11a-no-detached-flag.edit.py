# 11a: the engine encoder does not write `detached` (Engine.encAction, shared with figram trace)
EDITS = [("Figram/Engine.lean",
"++ field \"detached\" d (.bool true)))",
"++ field \"detached\" (d && false) (.bool true)))")]

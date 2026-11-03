# 7: entering the bot, a stay variant leads to the snapshot's message instead of the initial one
EDITS = [("Figram/Core.lean",
"    let T := m.resolve { s with state := m.initial } b.target\n    realizeAfter",
"    let T := m.resolve s b.target\n    realizeAfter")]

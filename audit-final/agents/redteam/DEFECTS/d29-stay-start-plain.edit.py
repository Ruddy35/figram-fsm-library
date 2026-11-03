# 29: entering the bot, a variant without a target is ignored (plain first entry) (Machine.enterBot)
EDITS = [("Figram/Core.lean",
"  match (if s.accepts ev then m.choose m.initial ev env else none) with\n  | some b =>\n    let T := m.resolve",
"  match (if s.accepts ev then (m.choose m.initial ev env).filter (·.target != .stay) else none) with\n  | some b =>\n    let T := m.resolve")]

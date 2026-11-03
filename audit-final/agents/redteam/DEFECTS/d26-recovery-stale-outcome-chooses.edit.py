# 26: in recovery an outcome without a wait still chooses a variant (Machine.enterBot)
EDITS = [("Figram/Core.lean",
"  match (if s.accepts ev then m.choose m.initial ev env else none) with\n  | some b =>\n    let T := m.resolve",
"  match m.choose m.initial ev env with\n  | some b =>\n    let T := m.resolve")]

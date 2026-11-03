# 22: `need` asks for the conditions of a stale outcome (Engine.missing)
EDITS = [("Figram/Engine.lean",
"  else if s.accepts ev && ownsTimer (m.exitOf s.state) ev then\n    (dedup",
"  else if (s.accepts ev || (match ev with | .outcome .. => true | _ => false)) && ownsTimer (m.exitOf s.state) ev then\n    (dedup")]

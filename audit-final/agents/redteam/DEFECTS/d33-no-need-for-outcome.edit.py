# 33: a step on an accepted outcome never asks for conditions (Engine.missing): missing ones are false
EDITS = [("Figram/Engine.lean",
"  else if s.accepts ev && ownsTimer (m.exitOf s.state) ev then\n    (dedup",
"  else if s.accepts ev && ownsTimer (m.exitOf s.state) ev && !(match ev with | .outcome .. => true | _ => false) then\n    (dedup")]

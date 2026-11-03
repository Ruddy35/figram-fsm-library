# 6: a stay timer firing does not consume the timer (shared Snapshot.consume)
EDITS = [("Figram/Core.lean",
"  | .timer id _ => { s with timers := erase s.timers id }\n  | _ => s",
"  | .timer _ _ => s\n  | _ => s")]

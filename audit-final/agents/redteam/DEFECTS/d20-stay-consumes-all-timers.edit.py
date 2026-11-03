# 20: a stay timer firing consumes all timers of the snapshot (shared consume)
EDITS = [("Figram/Core.lean",
"  | .timer id _ => { s with timers := erase s.timers id }\n  | _ => s",
"  | .timer _ _ => { s with timers := [] }\n  | _ => s")]

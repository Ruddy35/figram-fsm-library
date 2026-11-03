# 8a: recovery drops the waits (Machine.step)
EDITS = [("Figram/Core.lean",
"  if s.state ∉ m.states then m.enterBot s (s.timers.map (fun p => .cancelTimer p.1)) ev env",
"  if s.state ∉ m.states then m.enterBot { s with pending := [] } (s.timers.map (fun p => .cancelTimer p.1)) ev env")]

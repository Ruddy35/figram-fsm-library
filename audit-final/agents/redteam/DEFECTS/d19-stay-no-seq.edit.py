# 19: a variant without a target does not advance `seq` (shared stay)
EDITS = [("Figram/Core.lean",
"  realize (s.consume ev) s.state acts\n",
"  let r := realize (s.consume ev) s.state acts\n  ({ r.1 with seq := s.seq }, r.2)\n")]

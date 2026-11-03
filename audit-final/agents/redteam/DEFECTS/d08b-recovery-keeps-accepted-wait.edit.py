# 8b: recovery (entering the bot) does not remove the wait of an accepted outcome (Machine.enterBot)
EDITS = [("Figram/Core.lean",
"    realizeAfter (s.settle ev) T pre (b.actions ++ m.entryOf T)\n  | none => realizeAfter (s.settle ev) m.initial pre m.initActions",
"    realizeAfter s T pre (b.actions ++ m.entryOf T)\n  | none => realizeAfter s m.initial pre m.initActions")]

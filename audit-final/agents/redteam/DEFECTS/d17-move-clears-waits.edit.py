# 17: a move clears the waits (Machine.fire)
EDITS = [("Figram/Core.lean",
"    realizeClear (m.markBack s T) T (exit ++ staleCancels exit s) (b.actions ++ m.entryOf T)",
"    realizeClear { m.markBack s T with pending := [] } T (exit ++ staleCancels exit s) (b.actions ++ m.entryOf T)")]

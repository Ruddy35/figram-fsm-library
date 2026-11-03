# 38: a background action with outcomes records no wait (shared realizeOne)
EDITS = [("Figram/Core.lean",
"    ({ acc.1 with pending := addWait acc.1.pending n tok w }, acc.2 ++ [.call n d w tok])",
"    ({ acc.1 with pending := if d then acc.1.pending else addWait acc.1.pending n tok w }, acc.2 ++ [.call n d w tok])")]

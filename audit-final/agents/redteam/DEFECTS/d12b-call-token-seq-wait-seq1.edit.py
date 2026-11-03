# 12b: the reply gives the call the token `seq`, the wait records `seq + 1` (shared realizeOne)
EDITS = [("Figram/Core.lean",
"    ({ acc.1 with pending := addWait acc.1.pending n tok w }, acc.2 ++ [.call n d w tok])",
"    ({ acc.1 with pending := addWait acc.1.pending n tok w }, acc.2 ++ [.call n d w (tok - 1)])")]

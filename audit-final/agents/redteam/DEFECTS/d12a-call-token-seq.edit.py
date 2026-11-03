# 12a: the token of a call (and its wait) is `seq` instead of `seq + 1` (shared realizeOne)
EDITS = [("Figram/Core.lean",
"    ({ acc.1 with pending := addWait acc.1.pending n tok w }, acc.2 ++ [.call n d w tok])",
"    ({ acc.1 with pending := addWait acc.1.pending n (tok - 1) w }, acc.2 ++ [.call n d w (tok - 1)])")]

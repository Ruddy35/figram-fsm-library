# 15a: check does not enforce V6 (an action with outcomes twice in one `do`)
EDITS = [("Figram/Format.lean",
"  ++ dedup badOutcomeEvents ++ twiceInDo ++ c.lint",
"  ++ dedup badOutcomeEvents ++ twiceInDo.take 0 ++ c.lint")]

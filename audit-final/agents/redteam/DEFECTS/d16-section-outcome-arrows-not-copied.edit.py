# 16: section/root outcome arrows are not copied into the messages (Compile.leafRows)
EDITS = [("Figram/Compile.lean",
"  let tab := concatIndex ((ix.anc L).flatMap fun a => tr.concat a)",
"  let tab := concatIndex ((ix.anc L).flatMap fun a => (tr.concat a).filter fun p => a == L || !p.1.startsWith \"call:\")")]

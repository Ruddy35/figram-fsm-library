# 20r: defect 20 (a stay timer firing clears all timers) with the two unpinned helper proofs repaired
# without sorry: every pinned statement still holds, so the proof gate stays green.
EDITS = [
("Figram/Core.lean",
"  | .timer id _ => { s with timers := erase s.timers id }\n  | _ => s",
"  | .timer _ _ => { s with timers := [] }\n  | _ => s"),
("Figram/EngineProofs.lean",
"    exact ⟨hr, fun p hq => ht p (mem_erase hq), hb, hp⟩\n  | _ => exact h\n\ntheorem realizeOne_inv",
"    exact ⟨hr, fun p hq => absurd hq (by simp [Snapshot.consume]), hb, hp⟩\n  | _ => exact h\n\ntheorem realizeOne_inv"),
("Figram/OutcomeProofs.lean",
"  | timer id t => exact Engine.mem_erase h\n  | _ => exact h\n\nnamespace Machine",
"  | timer id t => exact absurd h (by simp [Snapshot.consume])\n  | _ => exact h\n\nnamespace Machine"),
]

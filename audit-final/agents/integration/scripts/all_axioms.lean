/-
AUDIT (integration): print the axioms of EVERY theorem declared in a `Figram*` module
(not only the 14 listed in verify.sh). Output lines: `AX <name> : [ax1, ax2, ...]`, sorted
by name, then a summary line. Two negative controls declared in THIS file (not in Figram)
must show `sorryAx` and the custom axiom, so a run that cannot see them is a broken detector.
Usage: lake env lean all_axioms.lean   (from the project root)
-/
import Lean
import Figram
import Figram.Api
open Lean Elab Command

theorem auditNegControlSorry : (1 : Nat) = 2 := sorry
axiom auditNegControlAxiom : (2 : Nat) = 3
theorem auditNegControlAx : (2 : Nat) = 3 := auditNegControlAxiom

#eval show CommandElabM Unit from do
  let env ← getEnv
  let mut rows : Array (String × String × Bool) := #[]
  let mut nmods := 0
  let std : List Name := [``propext, ``Quot.sound, ``Classical.choice]
  for i in [0:env.header.moduleNames.size] do
    let mod := env.header.moduleNames[i]!
    unless (`Figram).isPrefixOf mod || mod == `Main || mod == `EngineMain do continue
    nmods := nmods + 1
    let md := env.header.moduleData[i]!
    for ci in md.constants do
      if let .thmInfo _ := ci then
        let axs ← liftCoreM <| Lean.collectAxioms ci.name
        let axs := axs.qsort (fun a b => a.toString < b.toString)
        rows := rows.push (ci.name.toString, s!"{mod} : {axs.toList}", axs.all (std.contains ·))
  let sorted := rows.qsort (fun a b => a.1 < b.1)
  let mut bad := 0
  for (n, r, _) in sorted do
    IO.println s!"AX {n} @ {r}"
  for (n, r, ok) in sorted do
    unless ok do
      bad := bad + 1
      IO.println s!"NONSTANDARD {n} @ {r}"
  for c in [``auditNegControlSorry, ``auditNegControlAx] do
    let axs ← liftCoreM <| Lean.collectAxioms c
    let ok := axs.all (std.contains ·)
    IO.println s!"CONTROL {c} : {axs.toList} standard={ok}"
  IO.println s!"SUMMARY modules={nmods} theorems={rows.size} nonstandard={bad}"

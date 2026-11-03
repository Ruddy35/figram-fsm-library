/- Audit 5 (formal), item 3: which Core definitions the reference semantics `Chart.step` uses
directly (transitively through definition bodies). If one of them is wrong with respect to the
prose, `compile_correct` cannot notice: both sides use it.
Run: lake env lean /home/user/figram-audit/agents5/formal/SharedDeps.lean -/
import Figram
import Lean.Elab.Command
open Lean Elab Command

partial def reach (env : Environment) (todo : List Name) (seen : NameSet) : NameSet :=
  match todo with
  | [] => seen
  | n :: rest =>
    if seen.contains n then reach env rest seen else
    let seen := seen.insert n
    match env.find? n with
    | some (.defnInfo v) => reach env (v.value.getUsedConstants.toList ++ rest) seen
    | _ => reach env rest seen

elab "#shared" : command => do
  let env ← getEnv
  let chart := reach env [``Figram.Chart.step, ``Figram.Chart.startWith] {}
  let core := [``Figram.Snapshot.accepts, ``Figram.Snapshot.settle, ``Figram.Snapshot.consume,
    ``Figram.addWait, ``Figram.realizeOne, ``Figram.realize, ``Figram.stay, ``Figram.ownsTimer,
    ``Figram.staleCancels, ``Figram.guardOK, ``Figram.Machine.step, ``Figram.Machine.fire]
  for c in core do
    logInfo m!"{c}: used by Chart.step/startWith = {chart.contains c}"
#shared

/-
Strict axiom gate for Figram (audit proposal).

Run:  lake env lean audit/proofs/AxiomGate.lean
It enumerates EVERY theorem that lives in a module whose root is `Figram`
(taken from the environment, not from a hand-written list), collects its axioms
with `Lean.collectAxioms`, and fails (exit code 1) if any axiom outside
{propext, Quot.sound, Classical.choice} is used (this catches `sorryAx`,
`Lean.ofReduceBool`/`Lean.trustCompiler` from `native_decide`, and any custom
`axiom`). It also fails if a module of the project declares an `axiom`, and if a
theorem listed in audit/proofs/theorems.txt (extracted from the sources) is
missing from the environment or is not a theorem.
-/
import Figram
import Lean.Elab.Command
import Lean.Util.CollectAxioms

open Lean Elab Command

def allowedAxioms : List Name := [``propext, ``Quot.sound, ``Classical.choice]

def figramModule? (env : Environment) (n : Name) : Option Name := do
  let idx ← env.getModuleIdxFor? n
  let mod := env.header.moduleNames[idx.toNat]!
  if mod.getRoot == `Figram then some mod else none

elab "#strict_axiom_gate " path:str : command => do
  let env ← getEnv
  let txt ← IO.FS.readFile path.getString
  let srcNames : List Name := (txt.splitOn "\n").filterMap fun l =>
    match l.splitOn "\t" with
    | n :: _ => if n.isEmpty then none else some n.toName
    | [] => none
  if srcNames.isEmpty then throwError "theorem list {path.getString} is empty"
  let mut thms : Array Name := #[]
  let mut declaredAxioms : Array Name := #[]
  let mut opaques : Array Name := #[]
  let mut defs : Array Name := #[]
  for (n, ci) in env.constants.map₁.toList do
    if (figramModule? env n).isSome then
      match ci with
      | .thmInfo _ => thms := thms.push n
      | .axiomInfo _ => declaredAxioms := declaredAxioms.push n
      | .opaqueInfo _ => opaques := opaques.push n
      | .defnInfo _ => defs := defs.push n
      | _ => pure ()
  let mut failures : Array String := #[]
  -- 1. every theorem: axioms ⊆ allowed
  let mut histogram : Std.HashMap String Nat := {}
  for n in thms do
    let axs ← collectAxioms n
    let bad := axs.filter (fun a => !allowedAxioms.contains a)
    unless bad.isEmpty do
      failures := failures.push s!"theorem {n} uses non-standard axioms {bad.toList}"
    let key := toString (axs.qsort Name.lt).toList
    histogram := histogram.insert key (histogram.getD key 0 + 1)
  -- 2. definitions/opaques: report any non-standard axiom (informational + failure)
  for n in defs ++ opaques do
    let axs ← collectAxioms n
    let bad := axs.filter (fun a => !allowedAxioms.contains a)
    unless bad.isEmpty do
      failures := failures.push s!"definition {n} uses non-standard axioms {bad.toList}"
  -- 3. no axioms declared in project modules
  for a in declaredAxioms do
    failures := failures.push s!"project module declares axiom {a}"
  -- 4. every source-level theorem exists and is a theorem
  for n in srcNames do
    match env.find? n with
    | some (.thmInfo _) => pure ()
    | some _ => failures := failures.push s!"{n} from sources is not a theorem in the environment"
    | none => failures := failures.push s!"{n} from sources not found in the environment"
  let srcSet : NameSet := srcNames.foldl (·.insert ·) {}
  let extra := thms.filter (fun n => !srcSet.contains n && !n.isInternalDetail)
  logInfo m!"theorems in Figram modules (environment): {thms.size}; from sources: {srcNames.length}; \
    non-internal env theorems not in source list (auto-generated equation lemmas etc.): {extra.size}"
  logInfo m!"definitions: {defs.size}; opaque constants (partial/opaque defs): {opaques.size}; \
    declared axioms: {declaredAxioms.size}"
  logInfo m!"opaque constants: {(opaques.qsort Name.lt).toList}"
  for (k, v) in histogram.toList do
    logInfo m!"axiom set {k}: {v} theorems"
  if failures.isEmpty then
    logInfo m!"STRICT AXIOM GATE: PASS"
  else
    for f in failures do logError f
    throwError "STRICT AXIOM GATE: FAIL ({failures.size} problems)"

#strict_axiom_gate "audit/proofs/theorems.txt"

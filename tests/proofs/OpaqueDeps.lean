/- Which theorem STATEMENTS (types) depend, through definition bodies, on opaque constants
(partial defs)? Such statements mention functions whose logical value is unspecified, so their
hypotheses cannot be discharged by evaluation. Run: lake env lean audit/proofs/OpaqueDeps.lean -/
import Figram
import FigramAudit.Glue
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

elab "#opaque_deps" : command => do
  let env ← getEnv
  let isProj (n : Name) : Bool :=
    match env.getModuleIdxFor? n with
    | some i => let m := env.header.moduleNames[i.toNat]!; m.getRoot == `Figram || m.getRoot == `FigramAudit
    | none => false
  for (n, ci) in env.constants.map₁.toList do
    if isProj n && !n.isInternalDetail then
      if let .thmInfo v := ci then
        let r := reach env v.type.getUsedConstants.toList {}
        let ops := r.toList.filter fun c =>
          isProj c && (match env.find? c with | some (.opaqueInfo _) => true | _ => false)
        unless ops.isEmpty do logInfo m!"{n}: statement depends on opaque {ops}"
#opaque_deps

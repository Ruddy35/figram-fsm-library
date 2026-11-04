import Figram
/-!
Differential of `Doc.check` and `Doc.warnings` between 7239ab7 (old: V5/V6 on the partial
traversals `allTimers`/`allEvents`/`allVariants`; the outcome warning without the timer-owner
filter) and 598fec6 (new: the product code as built). The old definitions below are copied
verbatim from `git show 7239ab7:Figram/Format.lean` and `git show 7239ab7:Figram/Warnings.lean`.

  lake env lean --run OldNewCheck.lean LISTFILE   (one document path per line)

Prints one line per document: `same`, `decode-error`, or `DIFF check` / `DIFF warnings` with both
lists; at the end the counts. Exit 0 always (the caller reads the counts).
-/
open Figram Figram.Format

namespace Old

partial def allTimers : NodeDoc → List String
  | .message _ ts _ => ts.map (·.1)
  | .sec _ cs _ _ => cs.flatMap fun p => allTimers p.2

partial def allEvents : NodeDoc → List String
  | .message on _ _ => on.map (·.1)
  | .sec _ cs on _ => on.map (·.1) ++ cs.flatMap fun p => allEvents p.2

partial def allVariants (id : NodeId) : NodeDoc → List (NodeId × EventId × Variant)
  | .message on _ _ => on.flatMap fun p => p.2.map fun v => (id, p.1, v)
  | .sec _ cs on _ => (on.flatMap fun p => p.2.map fun v => (id, p.1, v))
      ++ cs.flatMap fun p => allVariants p.1 p.2

def check (d : Doc) : List String :=
  let c := d.toChart
  let branches := c.transitions.flatMap (·.branches)
  let undeclaredCond := (branches.filterMap (·.guard)).filterMap fun n =>
    if n = "" || d.conditions.any (·.1 == n) then none
    else some s!"condition `{n}` is not described in `conditions`"
  let undeclaredCall := (branches.flatMap (·.actions)).filterMap fun
    | .call n .. => if n = "" || d.calls.any (·.1 == n) then none
                    else some s!"action `{n}` is not described in `calls`"
    | _ => none
  let timers := allTimers d.bot
  let badTimerEvents := (allEvents d.bot).filterMap fun e =>
    if e.startsWith "timer:" && !(timers.contains (e.drop 6).toString) then
      some s!"event `{e}`: no such timer (the prefix `timer:` is reserved)"
    else none
  let outcomesOf (n : String) : Option (List String) := match lookup d.calls n with
    | some c => if c.outcomes.isEmpty then none else some ("failed" :: c.outcomes)
    | none => none
  let badOutcomeEvents := (allEvents d.bot).filterMap fun e =>
    if !e.startsWith "call:" then none
    else match splitOutcome (e.drop 5).toString with
      | none => some s!"event `{e}`: an outcome event is `call:<action>:<outcome>` (the prefix `call:` is reserved)"
      | some (n, o) =>
        if (outcomesOf n).any (·.contains o) then none
        else some s!"event `{e}`: `{n}` is not an action with the outcome `{o}` (the prefix `call:` is reserved)"
  let twiceInDo := (allVariants "bot" d.bot).flatMap fun (id, e, v) =>
    (dups (v.calls.filter fun n => (outcomesOf n).isSome)).map fun n =>
      s!"`{id}` → `{e}`: action `{n}` with outcomes occurs twice in `do`"
  let structural :=
    (if d.requires.all (· ∈ knownFeatures) then [] else ["document requires extensions this reader does not know"])
    ++ (match d.bot with | .sec .. => [] | .message .. => ["`bot` must be a section"])
    ++ mapErrors "conditions" (d.conditions.map (·.1))
    ++ mapErrors "calls" (d.calls.map (·.1))
    ++ d.calls.flatMap (fun p => callErrors p.1 p.2)
    ++ mapErrors "variables" (d.variables.map (·.1))
    ++ extErrors "document" docKeys d.ext
    ++ nodeErrors "bot" d.bot
  (if d.wfOK then [] else ["document violates the format rules (see the errors below)"])
  ++ structural ++ dedup undeclaredCond ++ dedup undeclaredCall ++ dedup badTimerEvents
  ++ dedup badOutcomeEvents ++ twiceInDo ++ c.lint

def outcomeWarnings (d : Doc) : List String := Id.run do
  let c := d.toChart
  let ix := c.index
  let m := c.compile
  let mut ws : Array String := #[]
  for t in c.transitions do
    for b in t.branches do
      for a in b.actions do
        match a with
        | .call n _ w _ =>
          if w != .free then
            let os := ((lookup d.calls n).map (·.outcomes)).getD [] ++ ["failed"]
            let waiting : List NodeId := match b.target with
              | .node x => [ix.enter x]
              | .stay => c.leafIds.filter fun l => (ix.anc l).contains t.src
              | _ => []
            for M in waiting do
              for o in os do
                if (m.row M (outcomeEvent n o)).isNone then
                  ws := ws.push (s!"outcome `{o}` of action `{n}` has no arrow on `{Chart.shown M}`: " ++
                    s!"the variant of `{Chart.shown t.src}` on `{t.event}` waits for it there " ++
                    "(an outcome without an arrow is ignored)")
        | _ => pure ()
  return ws.toList

def warnings (d : Doc) : List String := d.toChart.lintWarnings ++ outcomeWarnings d

end Old

def main (args : List String) : IO UInt32 := do
  let list ← IO.FS.readFile args[0]!
  let mut same := 0
  let mut diffC := 0
  let mut diffW := 0
  let mut decErr := 0
  let mut accepted := 0
  let mut rejected := 0
  for path in (list.splitOn "\n").filter (· ≠ "") do
    let txt ← IO.FS.readFile path
    match parse txt >>= decode with
    | .error _ => decErr := decErr + 1; IO.println s!"{path}: decode-error"
    | .ok d =>
      let n := d.check
      let o := Old.check d
      if n.isEmpty then accepted := accepted + 1 else rejected := rejected + 1
      if n != o then
        diffC := diffC + 1
        IO.println s!"{path}: DIFF check (accept new={n.isEmpty} old={o.isEmpty})\n  new: {n}\n  old: {o}"
      else if n.isEmpty then
        let nw := d.warnings
        let ow := Old.warnings d
        if nw != ow then
          diffW := diffW + 1
          IO.println s!"{path}: DIFF warnings\n  new-only: {nw.filter (· ∉ ow)}\n  old-only: {ow.filter (· ∉ nw)}"
        else same := same + 1
      else same := same + 1
  IO.println s!"documents {same + diffC + diffW + decErr}: same {same}, check differs {diffC}, warnings differ {diffW}, decode errors {decErr}; accepted {accepted}, rejected {rejected}"
  return 0

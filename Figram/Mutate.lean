import Figram.CoreProofs

/-!
# Machine mutations: measuring the strength of the test suite

Each mutation is a plausible bug in the implementation of the engine or the compiler.
A good set of scenarios must detect every mutation that changes the behaviour.
Usage: `figram-engine --mutants bot.json` prints the list,
`figram-engine --mutate <mutation> bot.json` runs the deliberately broken engine.
-/

namespace Figram.Mutate

/-- All variants of all arrows, in order. -/
def branches (m : Machine) : List FBranch := m.rows.flatMap (·.branches)

/-- Change the arrows, keeping the grouping by message; `f` gets the
running number of the arrow (in the order of `Machine.rows`). -/
def mapRows (m : Machine) (f : Nat → Row → Row) : Machine := Id.run do
  let mut k := 0
  let mut table : Array (NodeId × List Row) := #[]
  for (s, rs) in m.table do
    let mut out : Array Row := #[]
    for r in rs do
      out := out.push (f k r)
      k := k + 1
    table := table.push (s, out.toList)
  return { m with table := table.toList }

/-- Change the variants; `f` gets the running number of the variant (in the order of `branches`). -/
def mapBranches (m : Machine) (f : Nat → FBranch → FBranch) : Machine := Id.run do
  let mut k := 0
  let mut table : Array (NodeId × List Row) := #[]
  for (s, rs) in m.table do
    let mut out : Array Row := #[]
    for r in rs do
      let mut bs : Array FBranch := #[]
      for b in r.branches do
        bs := bs.push (f k b)
        k := k + 1
      out := out.push { r with branches := bs.toList }
    table := table.push (s, out.toList)
  return { m with table := table.toList }

/-- Arrow mutations change neither the messages nor the exit actions. -/
theorem mapBranches_states (m : Machine) (f : Nat → FBranch → FBranch) :
    (mapBranches m f).states = m.states := rfl
theorem mapBranches_exit (m : Machine) (f : Nat → FBranch → FBranch) :
    (mapBranches m f).exit = m.exit := rfl
theorem mapRows_states (m : Machine) (f : Nat → Row → Row) : (mapRows m f).states = m.states := rfl
theorem mapRows_exit (m : Machine) (f : Nat → Row → Row) : (mapRows m f).exit = m.exit := rfl

/-- **A mutant of an arrow for a timer the message does not own is equivalent** on
such events: both the original and the broken machine ignore the firing without
reading the arrows (`Machine.step_unowned_timer`). Through the protocol an event with
the key `timer:X` can only be a timer firing (`Engine.decEvent`), so the mutations
`redirect`/`drop-guard`/`reverse-actions`/`swap-branches` of the `timer:X` rows of
messages without X do not affect the behaviour. -/
theorem mapBranches_unowned_timer (m : Machine) (f : Nat → FBranch → FBranch) (s : Snapshot)
    (id : String) (tok : Nat) (env : HostEnv) (hs : s.state ∈ m.states)
    (h : (m.exitOf s.state).contains (.cancelTimer id) = false) :
    (mapBranches m f).step s (.timer id tok) env = m.step s (.timer id tok) env := by
  rw [Machine.step_unowned_timer m s id tok env hs h,
    Machine.step_unowned_timer (mapBranches m f) s id tok env hs h]

theorem mapRows_unowned_timer (m : Machine) (f : Nat → Row → Row) (s : Snapshot)
    (id : String) (tok : Nat) (env : HostEnv) (hs : s.state ∈ m.states)
    (h : (m.exitOf s.state).contains (.cancelTimer id) = false) :
    (mapRows m f).step s (.timer id tok) env = m.step s (.timer id tok) env := by
  rw [Machine.step_unowned_timer m s id tok env hs h,
    Machine.step_unowned_timer (mapRows m f) s id tok env hs h]

def callNames (m : Machine) : List String :=
  let names := ((branches m).flatMap (·.actions)).filterMap fun
    | .call n => some n
    | _ => none
  names.foldl (fun acc n => if acc.contains n then acc else acc ++ [n]) []

/-- The actions visible to the host (the internal "remember the place" is not). -/
def visible (as : List Action) : List Action :=
  as.filter fun | .remember .. => false | _ => true

/-- The list of mutations, each of which changes the observable behaviour. -/
def mutants (m : Machine) : List String :=
  let bs := (branches m).zipIdx
  (callNames m).map (s!"drop-call:{·}")
  ++ (bs.filterMap fun (b, k) => match b.target with
      | .node n => if m.states.any (· != n) then some s!"redirect:{k}" else none
      | .resume _ => some s!"redirect:{k}")
  ++ (bs.filterMap fun (b, k) => if b.actions.length ≥ 2 && b.actions.reverse != b.actions then
        some s!"reverse-actions:{k}" else none)
  ++ (bs.filterMap fun (b, k) => if b.guard.isSome then some s!"drop-guard:{k}" else none)
  ++ (m.rows.zipIdx.filterMap fun (r, k) => if r.branches.length ≥ 2 then some s!"swap-branches:{k}" else none)
  ++ (m.entry.zipIdx.filterMap fun (e, k) =>
        if (visible e.2).reverse != visible e.2 then some s!"reverse-entry:{k}" else none)
  ++ (if m.exit.any (!·.2.isEmpty) then ["drop-exit"] else [])
  ++ (if m.entry.any (·.2.any fun | .remember .. => true | _ => false) then ["drop-remember"] else [])
  ++ (if m.resumes.any (!·.members.isEmpty) then ["resume-default"] else [])
  ++ (if m.entry.any (·.2.any fun | .startTimer .. => true | _ => false) then ["drop-timer-start"] else [])

def apply (m : Machine) (spec : String) : Option Machine :=
  match spec.splitOn ":" with
  | ["drop-call", n] => some (mapBranches m fun _ b =>
      { b with actions := b.actions.filter (· != .call n) })
  | ["redirect", k] => k.toNat?.map fun k => mapBranches m fun i b =>
      if i != k then b else
      let other := (m.states.find? fun s => match b.target with
        | .node n => s != n | .resume _ => true).getD m.initial
      { b with target := .node other }
  | ["reverse-actions", k] => k.toNat?.map fun k => mapBranches m fun i b =>
      if i == k then { b with actions := b.actions.reverse } else b
  | ["drop-guard", k] => k.toNat?.map fun k => mapBranches m fun i b =>
      if i == k then { b with guard := none } else b
  | ["swap-branches", k] => k.toNat?.map fun k => mapRows m fun i r =>
      if i == k then { r with branches := r.branches.reverse } else r
  | ["reverse-entry", k] => k.toNat?.map fun k =>
      { m with entry := m.entry.zipIdx.map fun (e, i) => if i == k then (e.1, e.2.reverse) else e }
  | ["drop-exit"] => some { m with exit := m.exit.map fun e => (e.1, []) }
  | ["drop-remember"] => some { m with entry := m.entry.map fun e =>
      (e.1, e.2.filter fun | .remember .. => false | _ => true) }
  | ["resume-default"] => some { m with resumes := m.resumes.map ({ · with members := [] }) }
  | ["drop-timer-start"] => some { m with entry := m.entry.map fun e =>
      (e.1, e.2.filter fun | .startTimer .. => false | _ => true) }
  | _ => none

end Figram.Mutate

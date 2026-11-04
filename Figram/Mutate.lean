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

/-- Change the variants, given the message whose arrows they are and their running number. -/
def mapBranchesAt (m : Machine) (f : NodeId → Nat → FBranch → FBranch) : Machine := Id.run do
  let mut k := 0
  let mut table : Array (NodeId × List Row) := #[]
  for (s, rs) in m.table do
    let mut out : Array Row := #[]
    for r in rs do
      let mut bs : Array FBranch := #[]
      for b in r.branches do
        bs := bs.push (f s k b)
        k := k + 1
      out := out.push { r with branches := bs.toList }
    table := table.push (s, out.toList)
  return { m with table := table.toList }

/-- Change every call of action `n` (in the variants and in the first-entry actions). -/
def mapCalls (m : Machine) (n : String) (f : Bool → Wait → Bool × Wait) : Machine :=
  let g : Action → Action := fun
    | .call n' d w t => if n' == n then .call n' (f d w).1 (f d w).2 t else .call n' d w t
    | a => a
  { mapBranches m (fun _ b => { b with actions := b.actions.map g }) with
    initActions := m.initActions.map g }

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
    | .call n .. => some n
    | _ => none
  names.foldl (fun acc n => if acc.contains n then acc else acc ++ [n]) []

/-- The names of the actions with outcomes. -/
def waitNames (m : Machine) : List String :=
  let names := ((branches m).flatMap (·.actions) ++ m.initActions).filterMap fun
    | .call n _ w _ => if w != .free then some n else none
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
      | .resume _ | .back _ | .stay => some s!"redirect:{k}")
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
  ++ (if m.zone.any (fun z => m.zone.any (·.2 != z.2)) then ["one-zone"] else [])
  -- actions with outcomes and background actions: the flag, the wait and its kind
  ++ (callNames m).map (s!"detach-flip:{·}")
  ++ (waitNames m).map (s!"wait-free:{·}")
  ++ (waitNames m).map (s!"wait-swap:{·}")
  -- a variant without a target that leaves and enters its message again
  ++ (bs.filterMap fun (b, k) => if b.target == .stay then some s!"stay-reenter:{k}" else none)

/-- A mutation `<kind>` or `<kind>:<argument>`, split at the first `:` (the argument may be an
action name, which may contain `:`). -/
def splitSpec (spec : String) : String × Option String :=
  match spec.splitOn ":" with
  | [] => ("", none)
  | [k] => (k, none)
  | k :: rest => (k, some (":".intercalate rest))

/-- The mutations whose argument is the name of an action. -/
def namedKinds : List String := ["drop-call", "detach-flip", "wait-free", "wait-swap"]

def apply (m : Machine) (spec : String) : Option Machine :=
  match splitSpec spec with
  | ("drop-call", some n) => some (mapBranches m fun _ b =>
      { b with actions := b.actions.filter fun | .call n' .. => n' != n | _ => true })
  | ("redirect", some k) => k.toNat?.map fun k => mapBranches m fun i b =>
      if i != k then b else
      let other := (m.states.find? fun s => match b.target with
        | .node n => s != n | .resume _ | .back _ | .stay => true).getD m.initial
      { b with target := .node other }
  | ("reverse-actions", some k) => k.toNat?.map fun k => mapBranches m fun i b =>
      if i == k then { b with actions := b.actions.reverse } else b
  | ("drop-guard", some k) => k.toNat?.map fun k => mapBranches m fun i b =>
      if i == k then { b with guard := none } else b
  | ("swap-branches", some k) => k.toNat?.map fun k => mapRows m fun i r =>
      if i == k then { r with branches := r.branches.reverse } else r
  | ("reverse-entry", some k) => k.toNat?.map fun k =>
      { m with entry := m.entry.zipIdx.map fun (e, i) => if i == k then (e.1, e.2.reverse) else e }
  | ("drop-exit", none) => some { m with exit := m.exit.map fun e => (e.1, []) }
  | ("drop-remember", none) => some { m with entry := m.entry.map fun e =>
      (e.1, e.2.filter fun | .remember .. => false | _ => true) }
  | ("resume-default", none) => some { m with resumes := m.resumes.map ({ · with members := [] }) }
  | ("drop-timer-start", none) => some { m with entry := m.entry.map fun e =>
      (e.1, e.2.filter fun | .startTimer .. => false | _ => true) }
  | ("one-zone", none) => some { m with zone := m.zone.map fun e => (e.1, none) }
  | ("detach-flip", some n) => some (mapCalls m n fun d w => (!d, w))
  | ("wait-free", some n) => some (mapCalls m n fun d _ => (d, .free))
  | ("wait-swap", some n) => some (mapCalls m n fun d w => (d, match w with
      | .latest => .every
      | .every => .latest
      | .free => .free))
  | ("stay-reenter", some k) => k.toNat?.map fun k => mapBranchesAt m fun s i b =>
      if i == k && b.target == .stay then { b with target := .node s } else b
  | _ => none

end Figram.Mutate

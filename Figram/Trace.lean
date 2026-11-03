import Figram.Engine

/-!
# Reference scenarios

The expected results are computed by the **reference semantics of the chart** (`Chart.step`),
not by the compiled machine. The names of the needed conditions are also taken from the chart
(`guardNames`), so that the input of a scenario does not depend on the compiler under test.

The format of a scenario line (JSONL, one line per scenario):

```
{"name"?: "...", "from"?: <snapshot>,
 "start"?: {"event"?: "...", "guards"?: {...}, "snapshot": ..., "actions": [...]},
 "steps": [{"event": "...", "token"?: n, "guards": {...},
            "snapshot": ..., "actions": [...], "recovered"?: true}]}
```

With `from`, the scenario starts from that snapshot, otherwise from `start` (with its first
event, if `start` has one: the request `start` with `event` and `guards`).
-/

namespace Figram.Trace
open Figram.Format

def lcg (s : Nat) : Nat := (s * 6364136223846793005 + 1442695040888963407) % 2 ^ 64

/-- The names of the conditions needed for a step, by the reference semantics: the conditions
of the candidates up to and including the first unconditional one (the choice never gets further). -/
def guardNames (c : Chart) (s : Snapshot) (ev : Event) : List String :=
  if s.state ∉ c.leafIds then
    -- the step enters the bot again with the event, matched at the initial message
    match ev with
    | .named e => dedup ((untilUnguarded (·.guard) (c.cands (c.enter c.root) e)).filterMap (·.guard))
    | .timer .. => []
    | .outcome .. =>
      -- an outcome without a wait chooses nothing there
      if s.accepts ev then
        dedup ((untilUnguarded (·.guard) (c.cands (c.enter c.root) ev.key)).filterMap (·.guard))
      else []
  else if s.accepts ev && ownsTimer (c.exitActs s.state) ev then
    dedup ((untilUnguarded (·.guard) (c.cands s.state ev.key)).filterMap (·.guard))
  else []

/-- The index of the variant that fired among the candidates (for coverage). -/
def firedIndex (c : Chart) (s : Snapshot) (ev : Event) (env : HostEnv) : Option Nat :=
  if s.state ∈ c.leafIds && s.accepts ev && ownsTimer (c.exitActs s.state) ev then
    (c.cands s.state ev.key).findIdx? (fun b => guardOK env b.guard)
  else none

def envOf (gs : List (String × Bool)) : HostEnv := fun n => ((gs.find? (·.1 == n)).map (·.2)).getD false

/-- One step: the new snapshot and the JSON line of the step. -/
def stepJ (c : Chart) (s : Snapshot) (ev : Event) (gs : List (String × Bool)) : Snapshot × J :=
  let (s', acts) := c.step s ev (envOf gs)
  let recovered := decide (s.state ∉ c.leafIds)
  let tok : Obj := match ev with
    | .timer _ t => [("token", .num t 0)]
    | .outcome _ _ t => [("token", .num t 0)]
    | .named _ => []
  (s', .obj ([("event", .str ev.key)] ++ tok ++
    [("guards", .obj (gs.map fun p => (p.1, .bool p.2))),
     ("snapshot", Engine.encSnapshot s'), ("actions", Engine.encActions acts)]
    ++ field "recovered" recovered (.bool true)))

def startJ (c : Chart) : J :=
  let (s, a) := c.start
  .obj [("snapshot", Engine.encSnapshot s), ("actions", Engine.encActions a)]

/-- The names of the conditions needed to enter with event `e`, by the reference semantics. -/
def startGuardNames (c : Chart) (e : EventId) : List String :=
  dedup ((untilUnguarded (·.guard) (c.cands (c.enter c.root) e)).filterMap (·.guard))

/-- The first entry with event `e`: the new snapshot and the JSON of `start`. -/
def startWithJ (c : Chart) (e : EventId) (gs : List (String × Bool)) : Snapshot × J :=
  let (s, a) := c.startWith 0 (.named e) (envOf gs)
  (s, .obj [("event", .str e), ("guards", .obj (gs.map fun p => (p.1, .bool p.2))),
    ("snapshot", Engine.encSnapshot s), ("actions", Engine.encActions a)])

/-! ## Random scenario -/

/-- How "close" to the message the event is defined: 0 means on the message itself. -/
def depthOf (c : Chart) (L : NodeId) (e : EventId) : Nat :=
  ((c.anc L).findIdx? (fun a => !(c.branchesAt a e).isEmpty)).getD 99

structure Gen where
  c : Chart
  s : Snapshot
  r : Nat
  steps : Array J := #[]
  covered : List String := []

def Gen.rnd (g : Gen) (n : Nat) : Nat × Gen :=
  let r := lcg g.r
  ((r >>> 33) % max n 1, { g with r := r })

/-- The outcomes of action `call` that have an arrow somewhere in the chart, and `failed`. -/
def outcomesOf (c : Chart) (call : String) : List String :=
  "failed" :: c.events.filterMap fun e =>
    if e.startsWith "call:" then
      (splitOutcome (e.drop 5).toString).bind fun (n, o) => if n == call && o != "failed" then some o else none
    else none

/-- Choosing an event: the message's own events are 8 times as likely as global ones,
timers fire with the current or a stale token, a waited-for action reports an outcome (with the
token of its wait or a wrong one; half of the time one handled on the current message),
occasionally an unknown event. -/
def Gen.pickEvent (g : Gen) : Event × Gen := Id.run do
  let (roll, g) := g.rnd 100
  if roll < 15 && !g.s.timers.isEmpty then
    let (i, g) := g.rnd g.s.timers.length
    let (id, tok) := g.s.timers.getD i ("", 0)
    return (.timer id tok, g)
  if roll < 20 && !g.s.timers.isEmpty then
    let (i, g) := g.rnd g.s.timers.length
    let (id, tok) := g.s.timers.getD i ("", 0)
    return (.timer id (if tok > 0 then tok - 1 else tok + 1), g)
  if roll < 23 then return (.named "unknown:event", g)
  if roll < 50 && !g.s.pending.isEmpty then
    let (i, g) := g.rnd g.s.pending.length
    let (call, tok) := g.s.pending.getD i ("", 0)
    -- half of the time an outcome with an arrow on the current message, if there is one
    let outs := outcomesOf g.c call
    let L := g.s.state
    let here := outs.filter fun o => !(g.c.cands L (outcomeEvent call o)).isEmpty
    -- among them, preferably one with a variant not covered yet
    let here := match here.filter (fun o => (List.range (g.c.cands L (outcomeEvent call o)).length).any
        fun i => !g.covered.contains s!"{L}|{outcomeEvent call o}|{i}") with
      | [] => here
      | xs => xs
    let (coin, g) := g.rnd 2
    let outs := if coin == 0 && !here.isEmpty then here else outs
    let (j, g) := g.rnd outs.length
    let (wrong, g) := g.rnd 6
    return (.outcome call (outs.getD j "failed") (if wrong == 0 then tok + 1 else tok), g)
  let L := g.s.state
  let live := g.c.events.filter (fun e =>
    !e.startsWith "timer:" && !e.startsWith "call:" && !(g.c.cands L e).isEmpty)
  if live.isEmpty then return (.named "unknown:event", g)
  -- coverage guidance: an event with a variant at this message not covered yet weighs 4 times more
  let fresh (e : EventId) : Bool :=
    (List.range (g.c.cands L e).length).any fun i => !g.covered.contains s!"{L}|{e}|{i}"
  let weighted := live.flatMap fun e =>
    List.replicate (2 ^ (3 - min (depthOf g.c L e) 3) * (if fresh e then 4 else 1)) e
  let (i, g) := g.rnd weighted.length
  return (.named (weighted.getD i "unknown:event"), g)

def Gen.step (g : Gen) : Gen := Id.run do
  let (ev, g) := g.pickEvent
  let mut g := g
  let mut gs : List (String × Bool) := []
  for n in guardNames g.c g.s ev do
    let (b, g') := g.rnd 2
    g := g'
    gs := gs ++ [(n, b == 0)]
  -- coverage guidance: half of the time the conditions choose the first variant not covered yet
  let (aim, g') := g.rnd 2
  g := g'
  let L := g.s.state
  if aim == 0 && L ∈ g.c.leafIds then
    let cs := g.c.cands L ev.key
    match (List.range cs.length).find? (fun i => !g.covered.contains s!"{L}|{ev.key}|{i}") with
    | some i =>
      let earlier := (cs.take i).filterMap (·.guard)
      let mine := (cs.drop i).head?.bind (·.guard)
      gs := gs.map fun (n, v) =>
        if mine == some n then (n, true) else if earlier.contains n then (n, false) else (n, v)
    | none => pure ()
  let cov := match firedIndex g.c g.s ev (envOf gs) with
    | some i => [s!"{g.s.state}|{ev.key}|{i}"]
    | none => []
  let (s2, j) := stepJ g.c g.s ev gs
  let cov2 := cov.foldl (fun acc x => if acc.contains x then acc else x :: acc) g.covered
  return { g with s := s2, steps := g.steps.push j, covered := cov2 }

/-- A random scenario and the arrow variants covered by it and by `covered` (the variants covered
by earlier scenarios, which guide the choice of events towards the rest). -/
def random (c : Chart) (len seed : Nat) (covered : List String := []) : J × List String := Id.run do
  let mut g : Gen := { c := c, s := c.start.1, r := seed, covered := covered }
  for _ in [0:len] do
    g := g.step
  return (.obj [("start", startJ c), ("steps", .arr g.steps.toList)], g.covered)

/-- The total number of arrow variants of all messages (the denominator of coverage). -/
def totalBranches (c : Chart) : Nat :=
  c.leafIds.foldl (fun acc L => acc + c.events.foldl (fun a e => a + (c.cands L e).length) 0) 0

/-! ## Directed scenarios

The random scenarios reach deep messages by chance. A breadth-first search over the places
reachable from the first entry finds, for each arrow variant the random scenarios missed, the
shortest list of inputs that fires it; `figram trace` adds these as scenarios of the same form. -/

/-- The place of a snapshot for the search: everything but the counter and the tokens. -/
def placeKey (s : Snapshot) : String :=
  s!"{s.state}|{s.resume}|{s.timers.map (·.1)}|{s.back}|{s.pending.map (·.1)}"

/-- The conditions of a step from `s` on `ev` aimed at candidate `i`: its condition true, the
others false (so the first candidate whose condition holds is `i`, unless an earlier one has
no condition or the same one). -/
def aimAt (c : Chart) (s : Snapshot) (ev : Event) (i : Nat) : List (String × Bool) :=
  let mine := ((c.cands s.state ev.key).drop i).head?.bind (·.guard)
  (guardNames c s ev).map fun n => (n, mine == some n)

/-- The inputs tried from a snapshot: every named event, every active timer with its token,
every outcome of every wait, each with the conditions aimed at each candidate variant. -/
def edges (c : Chart) (s : Snapshot) : List (Event × List (String × Bool)) :=
  let evs : List Event :=
    (c.events.filter (fun e => !e.startsWith "timer:" && !e.startsWith "call:")).map .named
    ++ s.timers.map (fun p => .timer p.1 p.2)
    ++ s.pending.flatMap (fun p => (outcomesOf c p.1).map fun o => .outcome p.1 o p.2)
  evs.flatMap fun ev =>
    (List.range (c.cands s.state ev.key).length).map fun i => (ev, aimAt c s ev i)

abbrev Path := List (Event × List (String × Bool))

/-- Breadth-first search over at most `limit` places reachable from the first entry: for each
arrow variant not in `covered`, the shortest list of inputs found that fires it. The search
stops once `want` variants are found. -/
def search (c : Chart) (covered : List String) (limit want : Nat) : List (String × Path) := Id.run do
  let s0 := c.start.1
  let mut seen : Tab Unit := (Tab.empty 4096).insert (placeKey s0) ()
  let mut queue : Array (Snapshot × Path) := #[(s0, [])]
  let mut found : Tab Unit := Tab.empty 1024
  let mut out : Array (String × Path) := #[]
  let mut i := 0
  for _ in [0:limit] do
    if h : i < queue.size ∧ out.size < want then
      let (s, rpath) := queue[i]'h.1
      i := i + 1
      for (ev, gs) in edges c s do
        let rpath' := (ev, gs) :: rpath
        if let some k := firedIndex c s ev (envOf gs) then
          let key := s!"{s.state}|{ev.key}|{k}"
          unless covered.contains key || found.contains key do
            found := found.insert key ()
            out := out.push (key, rpath'.reverse)
        let s' := (c.step s ev (envOf gs)).1
        let pk := placeKey s'
        unless seen.contains pk do
          seen := seen.insert pk ()
          queue := queue.push (s', rpath')
  return out.toList

/-- The scenario of a list of inputs from the first entry. -/
def replay (c : Chart) (path : Path) : J := Id.run do
  let mut s := c.start.1
  let mut steps : Array J := #[]
  for (ev, gs) in path do
    let (s', j) := stepJ c s ev gs
    steps := steps.push j
    s := s'
  return .obj [("start", startJ c), ("steps", .arr steps.toList)]

/-- Scenarios for the arrow variants not in `covered` that the search reaches, and the variants
covered then. -/
def directed (c : Chart) (covered : List String) (limit : Nat := 3000) : List J × List String :=
  let want := totalBranches c - covered.length
  if want = 0 then ([], covered) else
  let found := search c covered limit want
  (found.map fun p => replay c p.2, covered ++ found.map (·.1))


/-! ## Scripted scenario -/

inductive In where
  | ev (name : String) (guards : List (String × Bool) := [])
  /-- As the first input of a scenario without `from`: `start` with this first event. -/
  | start (name : String) (guards : List (String × Bool) := [])
  /-- A timer firing with the current token. -/
  | timerNow (id : String)
  /-- A timer firing with a stale token. -/
  | timerStale (id : String) (token : Nat)
  /-- Outcome `outcome` of the newest call of action `call` (the token of its newest wait). -/
  | outcomeNow (call outcome : String) (guards : List (String × Bool) := [])
  /-- Outcome `outcome` of action `call` with a given token. -/
  | outcomeTok (call outcome : String) (token : Nat) (guards : List (String × Bool) := [])

def scripted (c : Chart) (name : String) (from? : Option Snapshot) (ins : List In) :
    J × Snapshot × List (List Action) := Id.run do
  -- the scenario gets exactly the conditions the entry or the step needs (by the reference semantics)
  let pick (names : List String) (given : List (String × Bool)) : List (String × Bool) :=
    names.map fun n => (n, ((given.find? (·.1 == n)).map (·.2)).getD false)
  let (s0, startHead, ins) := match from?, ins with
    | none, .start e given :: rest =>
      let gs := pick (startGuardNames c e) given
      let (s, j) := startWithJ c e gs
      (s, j, rest)
    | _, _ => (from?.getD c.start.1, startJ c, ins)
  let mut s := s0
  let mut steps : Array J := #[]
  let mut acts : List (List Action) := []
  for i in ins do
    let (ev, given) := match i with
      | .ev n gs | .start n gs => (Event.named n, gs)
      | .timerNow id => (Event.timer id ((lookup s.timers id).getD 0), [])
      | .timerStale id t => (Event.timer id t, [])
      | .outcomeNow c o gs => (Event.outcome c o (((s.pending.find? (·.1 == c)).map (·.2)).getD 0), gs)
      | .outcomeTok c o t gs => (Event.outcome c o t, gs)
    let gs := pick (guardNames c s ev) given
    let a := (c.step s ev (envOf gs)).2
    let (s', j) := stepJ c s ev gs
    steps := steps.push j
    acts := acts ++ [a]
    s := s'
  let head : Obj := match from? with
    | some f => [("from", Engine.encSnapshot f)]
    | none => [("start", startHead)]
  return (.obj ([("name", .str name)] ++ head ++ [("steps", .arr steps.toList)]), s, acts)

end Figram.Trace

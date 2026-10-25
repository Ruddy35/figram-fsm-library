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

/-- Choosing an event: the message's own events are 8 times as likely as global ones,
timers fire with the current or a stale token, occasionally an unknown event. -/
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
  let L := g.s.state
  let live := g.c.events.filter (fun e => !e.startsWith "timer:" && !(g.c.cands L e).isEmpty)
  if live.isEmpty then return (.named "unknown:event", g)
  let weighted := live.flatMap fun e =>
    List.replicate (2 ^ (3 - min (depthOf g.c L e) 3)) e
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
  let cov := match firedIndex g.c g.s ev (envOf gs) with
    | some i => [s!"{g.s.state}|{ev.key}|{i}"]
    | none => []
  let (s2, j) := stepJ g.c g.s ev gs
  let cov2 := cov.foldl (fun acc x => if acc.contains x then acc else x :: acc) g.covered
  return { g with s := s2, steps := g.steps.push j, covered := cov2 }

/-- A random scenario and the arrow variants it covers. -/
def random (c : Chart) (len seed : Nat) : J × List String := Id.run do
  let mut g : Gen := { c := c, s := c.start.1, r := seed }
  for _ in [0:len] do
    g := g.step
  return (.obj [("start", startJ c), ("steps", .arr g.steps.toList)], g.covered)

/-- The total number of arrow variants of all messages (the denominator of coverage). -/
def totalBranches (c : Chart) : Nat :=
  c.leafIds.foldl (fun acc L => acc + c.events.foldl (fun a e => a + (c.cands L e).length) 0) 0

/-! ## Scripted scenario -/

inductive In where
  | ev (name : String) (guards : List (String × Bool) := [])
  /-- As the first input of a scenario without `from`: `start` with this first event. -/
  | start (name : String) (guards : List (String × Bool) := [])
  /-- A timer firing with the current token. -/
  | timerNow (id : String)
  /-- A timer firing with a stale token. -/
  | timerStale (id : String) (token : Nat)

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

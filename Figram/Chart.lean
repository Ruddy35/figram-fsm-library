import Figram.Core

/-!
# The designer's level: the base and three simplifications

What the designer draws. The base is the same five building blocks as in the core:
message, event, arrow with variants (conditions are written as text),
variable, action. On top of it, three simplifications:

1. **Sections.** Messages can be put into sections, and sections into each other.
   An arrow drawn from a section works on every message inside it
   ("Cancel" once for the whole order, `/start` on the section around the whole bot).
   An arrow into a section leads to its initial message.
2. **"Resume where the user left off".** An arrow can lead not to a message
   but "into the section, where the user stopped".
3. **Timed reminder.** A timer on a message: it starts when the user arrives
   and is cancelled when the user leaves; a firing arrives as the event
   `timerEvent id`.

`Chart.step` is the **reference semantics** of the chart. The compiler (`Figram.Compile`)
unfolds the simplifications into the base, and the theorem `compile_correct` proves
that the behaviour is the same.

## Decisions of the specification

1. Bubbling: variants are collected from the message up through the enclosing sections;
   the first variant whose condition is true wins. If the message has an arrow
   but all its conditions are false, the event goes up to the enclosing section.
2. Action order: exit (cancel the message's timers) → the arrow's actions →
   entry (remember the place, start timers, send the message).
3. An arrow to the same message sends it again and restarts its timers.
4. The place is remembered on entry into a message: for every section above it
   into which at least one "resume" arrow leads.
   For an arrow from outside the section this is the same as remembering the place on exit;
   from inside the section such an arrow leads to the current message.
5. If the remembered message is no longer in the chart (a new version),
   "resume" leads to the section's initial message.
6. An event without a matching arrow is ignored.
7. A timer firing is accepted only with the token of the current run.
8. If the snapshot's message is no longer in the chart, the user starts the bot
   over; the timers of the old place are cancelled, remembered places are kept.
-/

namespace Figram

inductive NodeKind where
  | message
  /-- A section and its initial message (or nested section). -/
  | sec (initial : NodeId)
  deriving DecidableEq, Repr

structure Node where
  id : NodeId
  parent : Option NodeId
  kind : NodeKind
  deriving DecidableEq, Repr

/-- The target of an arrow in the designer's chart. -/
inductive Dest where
  /-- A message or a section (then its initial message). -/
  | node (n : NodeId)
  /-- "Resume the section where the user left off". -/
  | history (sec : NodeId)
  deriving DecidableEq, Repr

structure Branch where
  /-- The name of a condition written as text; none means "otherwise / always". -/
  guard : Option String
  target : Dest
  actions : List Action
  deriving DecidableEq, Repr

structure Transition where
  /-- A message or a section. -/
  src : NodeId
  event : EventId
  branches : List Branch
  deriving DecidableEq, Repr

/-- A reminder on a message. -/
structure Timer where
  id : String
  node : NodeId
  delayMs : Nat
  deriving DecidableEq, Repr

structure Chart where
  nodes : List Node
  /-- Where the user lands on the first entry. -/
  root : NodeId
  transitions : List Transition
  timers : List Timer
  deriving Repr

namespace Chart

def find (c : Chart) (n : NodeId) : Option Node :=
  c.nodes.find? (fun x => decide (x.id = n))

def parent (c : Chart) (n : NodeId) : Option NodeId :=
  (c.find n).bind (·.parent)

def ancAux (c : Chart) : Nat → NodeId → List NodeId
  | 0, n => [n]
  | k + 1, n => n :: match c.parent n with
    | none => []
    | some p => c.ancAux k p

/-- The message itself and all sections above it, bottom up. -/
def anc (c : Chart) (n : NodeId) : List NodeId :=
  c.ancAux c.nodes.length n

def enterAux (c : Chart) : Nat → NodeId → NodeId
  | 0, n => n
  | k + 1, n => match c.find n with
    | some ⟨_, _, .sec i⟩ => c.enterAux k i
    | _ => n

/-- Entering a node: for a section, descend through the initial messages. -/
def enter (c : Chart) (n : NodeId) : NodeId :=
  c.enterAux c.nodes.length n

def leafIds (c : Chart) : List NodeId :=
  (c.nodes.filter (fun x => decide (x.kind = .message))).map (·.id)

/-- The sections into which at least one "resume" arrow leads. -/
def histSecs (c : Chart) : List NodeId :=
  c.transitions.flatMap fun t => t.branches.filterMap fun b =>
    match b.target with
    | .history s => some s
    | .node _ => none

/-- The messages inside section `s` at any depth. -/
def histCands (c : Chart) (s : NodeId) : List NodeId :=
  c.leafIds.filter (fun l => decide (s ∈ c.anc l ∧ l ≠ s))

def timersOf (c : Chart) (n : NodeId) : List Timer :=
  c.timers.filter (fun t => decide (t.node = n))

/-- The entry actions of message `T`: remember the place in every section above it
into which a "resume" arrow leads; start the timers; send the message. -/
def entryActs (c : Chart) (T : NodeId) : List Action :=
  (c.anc T).filterMap (fun s =>
      if s ∈ c.histSecs ∧ s ≠ T then some (.remember s T) else none)
  ++ (c.timersOf T).map (fun t => .startTimer t.id t.delayMs 0)
  ++ [.send T]

/-- The exit actions of message `L`: cancel its timers. -/
def exitActs (c : Chart) (L : NodeId) : List Action :=
  (c.timersOf L).map (fun t => .cancelTimer t.id)

/-- Where "resume" leads. -/
def restoreTarget (c : Chart) (s : Snapshot) (sec : NodeId) : NodeId :=
  match lookup s.resume sec with
  | some v => if v ∈ c.histCands sec then v else c.enter sec
  | none => c.enter sec

def resolve (c : Chart) (s : Snapshot) : Dest → NodeId
  | .node n => c.enter n
  | .history sec => c.restoreTarget s sec

/-- The variants of the arrows of node `a` for event `e`. -/
def branchesAt (c : Chart) (a : NodeId) (e : EventId) : List Branch :=
  (c.transitions.filter (fun t => decide (t.src = a ∧ t.event = e))).flatMap (·.branches)

/-- All candidates for message `L`: its own first, then those of the sections
above it. This is "bubbling". -/
def cands (c : Chart) (L : NodeId) (e : EventId) : List Branch :=
  (c.anc L).flatMap (c.branchesAt · e)

def fire (c : Chart) (s : Snapshot) (b : Branch) : Snapshot × List Action :=
  let T := c.resolve s b.target
  realize s T (c.exitActs s.state ++ staleCancels (c.exitActs s.state) s ++ b.actions ++ c.entryActs T)

def initActs (c : Chart) : List Action := c.entryActs (c.enter c.root)

def recover (c : Chart) (s : Snapshot) : Snapshot × List Action :=
  realize s (c.enter c.root) (s.timers.map (fun p => .cancelTimer p.1) ++ c.initActs)

/-- **The reference semantics of the designer's chart.** -/
def step (c : Chart) (s : Snapshot) (ev : Event) (env : HostEnv) : Snapshot × List Action :=
  if s.state ∉ c.leafIds then c.recover s
  else if !(s.accepts ev && ownsTimer (c.exitActs s.state) ev) then (s, [])
  else
    match (c.cands s.state ev.key).find? (fun b => guardOK env b.guard) with
    | none => (s, [])
    | some b => c.fire s b

def start (c : Chart) : Snapshot × List Action := realize .fresh (c.enter c.root) c.initActs

end Chart
end Figram

/-!
# Core: the flat machine

This is everything the engine executes. The building blocks:

* **state** (`NodeId`) — the message the user is on;
* **event** (`Event`) — a button press, a text, a command or a timer firing;
* **arrow** (`Row`) with ordered variants (`FBranch`): the first variant whose
  condition holds wins;
* **condition** — the name of a developer's condition; the host computes its value;
* **action** (`Action`).

The target of a variant is a message or "resume the section where the user left
off" (`Target.resume`): the place is read from the snapshot during the step.

The user snapshot (`Snapshot`) holds the current message, the remembered places
of sections, the active timers with their tokens and the step counter.

A step is the pure function `Machine.step`. Effects are only returned to the host.

Arrows are stored grouped by message (`Machine.table`): a step looks up the
message first, then the event among its arrows, so a step costs
O(number of messages + number of events of the message), not O(number of all arrows).
-/

namespace Figram

abbrev NodeId := String
abbrev EventId := String

/-! ## Membership of a name in a list

Decidability of `n ∈ l` for lists of strings is a direct loop comparing strings
(the generic `List.elem` calls the comparison through a closure; an engine step
checks several times whether a message belongs to the machine). -/

def memStr (n : String) : List String → Bool
  | [] => false
  | x :: xs => x == n || memStr n xs

theorem memStr_iff (n : String) (l : List String) : memStr n l = true ↔ n ∈ l := by
  induction l with
  | nil => simp [memStr]
  | cons x xs ih =>
    rw [memStr, Bool.or_eq_true, ih, List.mem_cons, beq_iff_eq]
    exact ⟨fun h => h.elim (fun e => Or.inl e.symm) Or.inr, fun h => h.elim (fun e => Or.inl e.symm) Or.inr⟩

instance (priority := high) decMemStr (n : String) (l : List String) : Decidable (n ∈ l) :=
  decidable_of_iff _ (memStr_iff n l)

/-! ## Key-value tables -/

/-- The value of a key (the first entry). -/
def lookup {α β} [DecidableEq α] (t : List (α × β)) (k : α) : Option β :=
  (t.find? (fun p => decide (p.1 = k))).map (·.2)

/-- Store a value: the new entry goes first, older entries with the same key are removed. -/
def assign {α β} [DecidableEq α] (t : List (α × β)) (k : α) (v : β) : List (α × β) :=
  (k, v) :: t.filter (fun p => decide (p.1 ≠ k))

def erase {α β} [DecidableEq α] (t : List (α × β)) (k : α) : List (α × β) :=
  t.filter (fun p => decide (p.1 ≠ k))

/-! ## Events and actions -/

def timerEvent (id : String) : EventId := "timer:" ++ id

inductive Event where
  | named (name : EventId)
  /-- A firing of timer `id` started with token `token`. -/
  | timer (id : String) (token : Nat)
  deriving DecidableEq, Repr

def Event.key : Event → EventId
  | .named n => n
  | .timer id _ => timerEvent id

inductive Action where
  /-- Send a message. -/
  | send (n : NodeId)
  /-- Call the developer's code. -/
  | call (name : String)
  /-- Start a timer. The engine assigns the token when it executes the step. -/
  | startTimer (id : String) (ms : Nat) (token : Nat)
  | cancelTimer (id : String)
  /-- Internal: remember that in section `sec` the user is on `n`.
  Never given to the host. -/
  | remember (sec : NodeId) (n : NodeId)
  deriving DecidableEq, Repr

/-! ## Snapshot -/

structure Snapshot where
  /-- The current message. -/
  state : NodeId
  /-- Remembered places: section → message. -/
  resume : List (NodeId × NodeId)
  /-- Active timers: name → token of the run. -/
  timers : List (String × Nat)
  /-- The number of the last transition. Grows by 1 with every transition;
  serves as the timer token and as the basis of the host's idempotency keys. -/
  seq : Nat
  deriving DecidableEq, Repr

def Snapshot.fresh : Snapshot := ⟨"", [], [], 0⟩

/-- Whether the event is accepted: a timer firing only with the current token. -/
def Snapshot.accepts (s : Snapshot) : Event → Bool
  | .named _ => true
  | .timer id tok => decide (lookup s.timers id = some tok)

/-- One step of executing actions: internal ones are applied to the snapshot,
the others go to the host; timers get their token. -/
def realizeOne (tok : Nat) (acc : Snapshot × List Action) : Action → Snapshot × List Action
  | .remember sec n => ({ acc.1 with resume := assign acc.1.resume sec n }, acc.2)
  | .startTimer id ms _ =>
    ({ acc.1 with timers := assign acc.1.timers id tok }, acc.2 ++ [.startTimer id ms tok])
  | .cancelTimer id => ({ acc.1 with timers := erase acc.1.timers id }, acc.2 ++ [.cancelTimer id])
  | a => (acc.1, acc.2 ++ [a])

/-- Cancel the snapshot's timers that `exit` does not cancel (leftovers of a previous
document version: docs/host-contract.md, section 6). Within one version the list is empty. -/
def staleCancels (exit : List Action) (s : Snapshot) : List Action :=
  (s.timers.filter (fun p => !exit.contains (.cancelTimer p.1))).map (fun p => .cancelTimer p.1)

/-- A firing of timer `id` belongs to the message whose exit cancels that timer. -/
def ownsTimer (exit : List Action) : Event → Bool
  | .named _ => true
  | .timer id _ => exit.contains (.cancelTimer id)

/-- A transition to `T` with actions `acts`: the new snapshot and the actions for the host. -/
def realize (s : Snapshot) (T : NodeId) (acts : List Action) : Snapshot × List Action :=
  acts.foldl (realizeOne (s.seq + 1)) ({ s with state := T, seq := s.seq + 1 }, [])

/-! ## Machine -/

/-- The values of the developer's conditions, supplied by the host. -/
abbrev HostEnv := String → Bool

def guardOK (env : HostEnv) : Option String → Bool
  | none => true
  | some n => env n

/-- The variants up to and including the first one without a condition: only these
can be chosen, the rest are shadowed. -/
def untilUnguarded {α} (g : α → Option String) : List α → List α
  | [] => []
  | b :: bs => match g b with
    | none => [b]
    | some _ => b :: untilUnguarded g bs

inductive Target where
  | node (n : NodeId)
  /-- "Resume section `sec` where the user left off". -/
  | resume (sec : NodeId)
  deriving DecidableEq, Repr

/-- A variant of an arrow: condition, target, the arrow's actions. -/
structure FBranch where
  guard : Option String
  target : Target
  actions : List Action
  deriving DecidableEq, Repr

structure Row where
  src : NodeId
  event : EventId
  branches : List FBranch
  deriving DecidableEq, Repr

/-- How to resume a section: its initial message and the messages inside it. -/
structure ResumeSpec where
  sec : NodeId
  default : NodeId
  members : List NodeId
  deriving DecidableEq, Repr

structure Machine where
  states : List NodeId
  initial : NodeId
  initActions : List Action
  /-- Arrows by message: message → its arrows, one per event. -/
  table : List (NodeId × List Row)
  resumes : List ResumeSpec
  /-- Entry actions of a message: remember the place, start timers, send. -/
  entry : List (NodeId × List Action)
  /-- Exit actions of a message: cancel its timers. -/
  exit : List (NodeId × List Action)
  deriving Repr

namespace Machine

/-- All arrows of the machine in a row (message by message). -/
def rows (m : Machine) : List Row := m.table.flatMap (·.2)

/-- The arrow of message `s` for event `e`: look up the message, then the event. -/
def row (m : Machine) (s : NodeId) (e : EventId) : Option Row :=
  ((lookup m.table s).getD []).find? (fun r => decide (r.event = e))

def resolve (m : Machine) (s : Snapshot) : Target → NodeId
  | .node n => n
  | .resume sec =>
    match m.resumes.find? (fun r => decide (r.sec = sec)) with
    | none => sec
    | some r =>
      match lookup s.resume sec with
      | some v => if v ∈ r.members then v else r.default
      | none => r.default

def entryOf (m : Machine) (n : NodeId) : List Action := (lookup m.entry n).getD []
def exitOf (m : Machine) (n : NodeId) : List Action := (lookup m.exit n).getD []

def fire (m : Machine) (s : Snapshot) (b : FBranch) : Snapshot × List Action :=
  let T := m.resolve s b.target
  realize s T (m.exitOf s.state ++ staleCancels (m.exitOf s.state) s ++ b.actions ++ m.entryOf T)

/-- The snapshot points to a message that no longer exists: start the bot over,
cancelling the timers of the old place. Remembered places are kept. -/
def recover (m : Machine) (s : Snapshot) : Snapshot × List Action :=
  realize s m.initial (s.timers.map (fun p => .cancelTimer p.1) ++ m.initActions)

/-- **A step of the machine.** -/
def step (m : Machine) (s : Snapshot) (ev : Event) (env : HostEnv) : Snapshot × List Action :=
  if s.state ∉ m.states then m.recover s
  else if !(s.accepts ev && ownsTimer (m.exitOf s.state) ev) then (s, [])
  else
    match m.row s.state ev.key with
    | none => (s, [])
    | some r =>
      match r.branches.find? (fun b => guardOK env b.guard) with
      | none => (s, [])
      | some b => m.fire s b

/-- The user's first entry. -/
def start (m : Machine) : Snapshot × List Action := realize .fresh m.initial m.initActions

/-- The developer's conditions needed for a step from `st` on the event with key `e`:
the conditions of the variants up to and including the first one without a condition.
Later variants cannot be chosen, so their conditions do not affect the step (`step_guard_local`). -/
def requiredGuards (m : Machine) (st : NodeId) (e : EventId) : List String :=
  match m.row st e with
  | none => []
  | some r => (untilUnguarded (·.guard) r.branches).filterMap (·.guard)

/-- The conditions whose values are needed to choose a variant when the values of
some conditions are already known (`known`): the variants are scanned in order up to
one without a condition or one whose condition is known to be true. -/
def neededIn (known : String → Option Bool) : List FBranch → List String
  | [] => []
  | b :: bs => match b.guard with
    | none => []
    | some g => match known g with
      | some true => []
      | some false => neededIn known bs
      | none => g :: neededIn known bs

/-- The missing conditions for a step from `st` on event `e` given `known`
(always a part of `requiredGuards`). -/
def neededGuards (m : Machine) (st : NodeId) (e : EventId) (known : String → Option Bool) :
    List String :=
  match m.row st e with
  | none => []
  | some r => neededIn known r.branches

def targetOK (m : Machine) : Target → Bool
  | .node n => decide (n ∈ m.states)
  | .resume sec => m.resumes.any (fun r => decide (r.sec = sec))

/-- The export check: all targets exist. -/
def wf (m : Machine) : Bool :=
  decide (m.initial ∈ m.states) &&
  m.rows.all (fun r => r.branches.all (fun b => m.targetOK b.target)) &&
  m.resumes.all (fun r => decide (r.default ∈ m.states) && r.members.all (fun v => decide (v ∈ m.states)))

end Machine
end Figram

/-!
# Core: the flat machine

This is everything the engine executes. The building blocks:

* **state** (`NodeId`) — the message the user is on;
* **event** (`Event`) — a button press, a text, a command, a timer firing or the outcome of an
  action;
* **arrow** (`Row`) with ordered variants (`FBranch`): the first variant whose
  condition holds wins;
* **condition** — the name of a developer's condition; the host computes its value;
* **action** (`Action`).

The target of a variant is a message, "resume the section where the user left
off" (`Target.resume`), "back to the previous section" (`Target.back`) — the place
is read from the snapshot during the step — or none (`Target.stay`): the variant's actions
without a move.

The user snapshot (`Snapshot`) holds the current message, the remembered places
of sections, the active timers with their tokens, the step counter, the place to
go back to and the waits for the outcomes of actions.

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

/-- The event of outcome `o` of action `c`: `call:<c>:<o>`. -/
def outcomeEvent (c o : String) : EventId := "call:" ++ c ++ ":" ++ o

/-- `<c>:<o>` split at its last `:` into the action and the outcome, both non-empty (an outcome
name contains no `:`, an action name may). -/
def splitOutcome (s : String) : Option (String × String) :=
  let r := s.toList.reverse
  match r.dropWhile (· != ':') with
  | [] => none
  | _ :: c =>
    let o := r.takeWhile (· != ':')
    if c.isEmpty || o.isEmpty then none else some (String.ofList c.reverse, String.ofList o.reverse)

inductive Event where
  | named (name : EventId)
  /-- A firing of timer `id` started with token `token`. -/
  | timer (id : String) (token : Nat)
  /-- Outcome `outcome` of action `call` performed with token `token`. -/
  | outcome (call : String) (outcome : String) (token : Nat)
  deriving DecidableEq, Repr

def Event.key : Event → EventId
  | .named n => n
  | .timer id _ => timerEvent id
  | .outcome c o _ => outcomeEvent c o

/-- Whether a step waits for the outcome of an action. -/
inductive Wait where
  /-- The action has no outcomes. -/
  | free
  /-- Only the latest call of the action is answered: a new call replaces the wait. -/
  | latest
  /-- Every call of the action is answered once. -/
  | every
  deriving DecidableEq, Repr

inductive Action where
  /-- Send a message. -/
  | send (n : NodeId)
  /-- Call the developer's code. `detached`: a background action, which the host may run later
  and in any order (it does not affect the machine). An action with outcomes (`wait ≠ .free`)
  gets the token of the step, which records a wait in the snapshot; the engine assigns the
  token when it executes the step. -/
  | call (name : String) (detached : Bool) (wait : Wait) (token : Nat)
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
  /-- The place to go back to: the message the user left when they last crossed a section
  boundary (`Machine.markBack`). -/
  back : Option NodeId
  /-- Waits for outcomes: action → token of the call, the newest first. -/
  pending : List (String × Nat)
  deriving DecidableEq, Repr

def Snapshot.fresh : Snapshot := ⟨"", [], [], 0, none, []⟩

/-- The snapshot with another place to go back to. -/
def Snapshot.withBack (s : Snapshot) (b : Option NodeId) : Snapshot := { s with back := b }

/-- Whether the event is accepted: a timer firing only with the current token, an outcome only
with the token of a wait for its action. -/
def Snapshot.accepts (s : Snapshot) : Event → Bool
  | .named _ => true
  | .timer id tok => decide (lookup s.timers id = some tok)
  | .outcome c _ tok => decide ((c, tok) ∈ s.pending)

/-- An outcome removes its wait: each wait accepts one outcome. -/
def Snapshot.settle (s : Snapshot) : Event → Snapshot
  | .outcome c _ tok => { s with pending := s.pending.filter (fun p => decide (p ≠ (c, tok))) }
  | _ => s

/-- A timer firing handled without a move consumes the timer. -/
def Snapshot.consume (s : Snapshot) : Event → Snapshot
  | .timer id _ => { s with timers := erase s.timers id }
  | _ => s

/-- The waits after action `n` with token `tok` is performed. -/
def addWait (pend : List (String × Nat)) (n : String) (tok : Nat) : Wait → List (String × Nat)
  | .free => pend
  | .latest => assign pend n tok
  | .every => (n, tok) :: pend.filter (fun p => decide (p ≠ (n, tok)))

/-- One step of executing actions: internal ones are applied to the snapshot,
the others go to the host; timers and actions get their token, actions with outcomes record
their wait. -/
def realizeOne (tok : Nat) (acc : Snapshot × List Action) : Action → Snapshot × List Action
  | .remember sec n => ({ acc.1 with resume := assign acc.1.resume sec n }, acc.2)
  | .startTimer id ms _ =>
    ({ acc.1 with timers := assign acc.1.timers id tok }, acc.2 ++ [.startTimer id ms tok])
  | .cancelTimer id => ({ acc.1 with timers := erase acc.1.timers id }, acc.2 ++ [.cancelTimer id])
  | .call n d w _ =>
    ({ acc.1 with pending := addWait acc.1.pending n tok w }, acc.2 ++ [.call n d w tok])
  | a => (acc.1, acc.2 ++ [a])

/-- Cancel the snapshot's timers that `exit` does not cancel (leftovers of a previous
document version: docs/host-contract.md, section 6). Within one version the list is empty. -/
def staleCancels (exit : List Action) (s : Snapshot) : List Action :=
  (s.timers.filter (fun p => !exit.contains (.cancelTimer p.1))).map (fun p => .cancelTimer p.1)

/-- A firing of timer `id` belongs to the message whose exit cancels that timer. -/
def ownsTimer (exit : List Action) : Event → Bool
  | .timer id _ => exit.contains (.cancelTimer id)
  | _ => true

/-- A transition to `T` with actions `acts`: the new snapshot and the actions for the host.
This is the specification: every cancelled timer is a pass over the timers and every action a
copy of the reply, so it takes time quadratic in the number of actions. The machine runs
`realizeClear` where that number grows with the snapshot. -/
def realize (s : Snapshot) (T : NodeId) (acts : List Action) : Snapshot × List Action :=
  acts.foldl (realizeOne (s.seq + 1)) ({ s with state := T, seq := s.seq + 1 }, [])

def Action.isCancel : Action → Bool
  | .cancelTimer _ => true
  | _ => false

/-- `realize s T (pre ++ acts)` for a `pre` that only cancels timers and cancels every timer of
`s` (`realize_clear` in `Figram.CoreProofs`): the timers are cleared at once and `pre` goes in
front of the reply. Leaving a message, recovery and `restart` cancel all the snapshot's timers
this way, so they take time linear in the size of the snapshot. -/
def realizeClear (s : Snapshot) (T : NodeId) (pre acts : List Action) : Snapshot × List Action :=
  let r := realize { s with timers := [] } T acts
  (r.1, pre ++ r.2)

/-- `realize s T (pre ++ acts)` (`realizeAfter_eq`), with the timers cleared at once when `pre`
cancels exactly the snapshot's timers, as recovery does. -/
def realizeAfter (s : Snapshot) (T : NodeId) (pre acts : List Action) : Snapshot × List Action :=
  if pre = s.timers.map (fun p => .cancelTimer p.1) then realizeClear s T pre acts
  else realize s T (pre ++ acts)

/-- **A variant without a target**: its actions without a move — the message is neither left
nor entered, the places stay; a timer firing handled this way is consumed. -/
def stay (s : Snapshot) (ev : Event) (acts : List Action) : Snapshot × List Action :=
  realize (s.consume ev) s.state acts

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
  /-- "Back to the previous section": the place to go back to (`Snapshot.back`) if it is a
  message of the machine, otherwise `fallback`. -/
  | back (fallback : Target)
  /-- No move: the variant's actions on the current message (`stay`). -/
  | stay
  deriving DecidableEq, Repr

/-- The target without "back": where a "back" leads when there is no place to go back to. -/
def Target.base : Target → Target
  | .back fb => fb.base
  | t => t

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
  /-- The section that directly contains a message (`none` outside any section). -/
  zone : List (NodeId × Option NodeId)
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
  | .back fb =>
    match s.back with
    | some v => if v ∈ m.states then v else m.resolve s fb
    | none => m.resolve s fb
  | .stay => s.state

def entryOf (m : Machine) (n : NodeId) : List Action := (lookup m.entry n).getD []
def exitOf (m : Machine) (n : NodeId) : List Action := (lookup m.exit n).getD []
def zoneOf (m : Machine) (n : NodeId) : Option NodeId := (lookup m.zone n).getD none

/-- A move from the snapshot's message to `T` that crosses a section boundary (the sections
that directly contain the two messages differ) makes the message left the place to go back
to; a move inside a section keeps it. -/
def markBack (m : Machine) (s : Snapshot) (T : NodeId) : Snapshot :=
  if m.zoneOf s.state = m.zoneOf T then s else s.withBack (some s.state)

/-- A move along variant `b`: `realize` of the exit actions, the cancels of the snapshot's
other timers, the variant's actions and the entry actions (`fire_eq_realize`). When the exit
actions only cancel timers (always in a compiled machine), all the snapshot's timers are cleared
at once. -/
def fire (m : Machine) (s : Snapshot) (b : FBranch) : Snapshot × List Action :=
  let T := m.resolve s b.target
  let exit := m.exitOf s.state
  if exit.all Action.isCancel then
    realizeClear (m.markBack s T) T (exit ++ staleCancels exit s) (b.actions ++ m.entryOf T)
  else realize (m.markBack s T) T (exit ++ staleCancels exit s ++ b.actions ++ m.entryOf T)

/-- Start the bot over from snapshot `s` without an event (`restart`): cancel its timers,
keep its remembered places and the place to go back to. -/
def recover (m : Machine) (s : Snapshot) : Snapshot × List Action :=
  realizeClear s m.initial (s.timers.map (fun p => .cancelTimer p.1)) m.initActions

/-- The user's first entry. -/
def start (m : Machine) : Snapshot × List Action := realize .fresh m.initial m.initActions

/-- The variant chosen for a named event or an outcome at message `n`, by the arrows of `n`
(with those of its sections). A timer firing chooses nothing: it belongs to a message the user
entered. -/
def choose (m : Machine) (n : NodeId) (ev : Event) (env : HostEnv) : Option FBranch :=
  match ev with
  | .timer .. => none
  | _ =>
    match m.row n ev.key with
    | none => none
    | some r => r.branches.find? (fun b => guardOK env b.guard)

/-- **Entering the bot with an event.** The event is matched as if the user stood on the
initial message, which is neither entered nor left: if a variant is chosen, `pre`, the
variant's actions and the entry of its target (a variant without a target leads to the initial
message); otherwise `pre` and the first-entry actions. An outcome without a wait in `s` chooses
nothing; an accepted one removes its wait. `s` gives the counter, the remembered places, the
place to go back to and the waits, which entering the bot does not change otherwise (the user
leaves no message of the machine). -/
def enterBot (m : Machine) (s : Snapshot) (pre : List Action) (ev : Event) (env : HostEnv) :
    Snapshot × List Action :=
  match (if s.accepts ev then m.choose m.initial ev env else none) with
  | some b =>
    let T := m.resolve { s with state := m.initial } b.target
    realizeAfter (s.settle ev) T pre (b.actions ++ m.entryOf T)
  | none => realizeAfter (s.settle ev) m.initial pre m.initActions

/-- The user's first entry with a first event, the counter continued from `n`. -/
def startWith (m : Machine) (n : Nat) (ev : Event) (env : HostEnv) : Snapshot × List Action :=
  m.enterBot ⟨"", [], [], n, none, []⟩ [] ev env

/-- **A step of the machine.** If the snapshot's message no longer exists, the user enters the
bot again with the event (`enterBot`), the timers of the old place cancelled and the remembered
places kept. An accepted outcome removes its wait, also when no variant is chosen. A variant
without a target acts without a move (`stay`). -/
def step (m : Machine) (s : Snapshot) (ev : Event) (env : HostEnv) : Snapshot × List Action :=
  if s.state ∉ m.states then m.enterBot s (s.timers.map (fun p => .cancelTimer p.1)) ev env
  else if !(s.accepts ev && ownsTimer (m.exitOf s.state) ev) then (s, [])
  else
    match m.row s.state ev.key with
    | none => (s.settle ev, [])
    | some r =>
      match r.branches.find? (fun b => guardOK env b.guard) with
      | none => (s.settle ev, [])
      | some b => if b.target = .stay then stay (s.settle ev) ev b.actions else m.fire (s.settle ev) b

/-- The conditions of the arrow of message `n` for the event with key `e`: those of the
variants up to and including the first one without a condition. Later variants cannot be
chosen, so their conditions do not affect the choice (`choose_guard_local`). -/
def rowGuards (m : Machine) (n : NodeId) (e : EventId) : List String :=
  match m.row n e with
  | none => []
  | some r => (untilUnguarded (·.guard) r.branches).filterMap (·.guard)

/-- The developer's conditions needed for a step from `st` on the event with key `e`: those of
the arrow of `st` or, if `st` no longer exists, of the initial message (the step enters the bot
again). They are all the step depends on (`step_guard_local`). -/
def requiredGuards (m : Machine) (st : NodeId) (e : EventId) : List String :=
  m.rowGuards (if st ∈ m.states then st else m.initial) e

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
  | .back fb => m.targetOK fb
  | .stay => true

/-- The export check: all targets exist. -/
def wf (m : Machine) : Bool :=
  decide (m.initial ∈ m.states) &&
  m.rows.all (fun r => r.branches.all (fun b => m.targetOK b.target)) &&
  m.resumes.all (fun r => decide (r.default ∈ m.states) && r.members.all (fun v => decide (v ∈ m.states)))

end Machine
end Figram

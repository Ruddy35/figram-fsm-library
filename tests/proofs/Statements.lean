/-
Pinned statements of the theorems named in docs/proofs.md and docs/format.md.
Each `example` re-states the theorem in full (as a reader would write it) and is
closed by the theorem itself: if a theorem is weakened, renamed or its
hypotheses change, this file stops compiling. Run: lake env lean tests/proofs/Statements.lean
-/
import Figram
open Figram Figram.Format Figram.Engine

-- Semantics / compiler
example : ∀ (c : Chart), c.compile.wf = true → ∀ (s : Snapshot) (ev : Event) (env : HostEnv),
    c.compile.step s ev env = c.step s ev env := @Chart.compile_correct
example : ∀ (c : Chart), c.compile.wf = true → ∀ (s : Snapshot) (inp : List Input),
    c.compile.run s inp = c.run s inp := @Chart.run_correct
example : ∀ (c : Chart), c.compile.wf = true → ∀ (inp : List Input),
    c.compile.run c.compile.start.1 inp = c.run c.start.1 inp := @Chart.session_correct
example : ∀ (c : Chart), c.compile.start = c.start := @Chart.start_correct
example : ∀ (c : Chart), c.compile.wf = true → ∀ (s : Snapshot) (x : Input) (rest : List Input),
    (c.run s (x :: rest)).1.state ∈ c.leafIds := @Chart.chart_run_valid
example : ∀ (m : Machine), m.wf = true → ∀ (s : Snapshot) (ev : Event) (env : HostEnv),
    (m.step s ev env).1.state ∈ m.states := @Machine.step_state_valid
example : ∀ (m : Machine), m.wf = true → m.start.1.state ∈ m.states := @Machine.start_state_valid
example : ∀ (m : Machine) (s : Snapshot) (ev : Event) (env₁ env₂ : HostEnv),
    (∀ n ∈ m.requiredGuards s.state ev.key, env₁ n = env₂ n) →
    m.step s ev env₁ = m.step s ev env₂ := @Machine.step_guard_local

-- Format (all on the JSON *model* `J`, never on text)
example : ∀ (d : Doc), DocWF d → decode (encode d) = .ok d := @decode_encode
example : ∀ (d₁ d₂ : Doc), DocWF d₁ → DocWF d₂ → encode d₁ = encode d₂ → d₁ = d₂ :=
  @encode_injective
example : ∀ (d : Doc), d.check = [] → decode (encode d) = .ok d := @check_roundtrip
example : ∀ (j : J) (d : Doc), decode j = .ok d → ∀ f ∈ d.requires, f ∈ knownFeatures :=
  @decode_requires
example : ∀ (d : Doc), d.strip.toChart = d.toChart := @toChart_strip
example : ∀ (d : Doc) (conds vars : List (String × String)) (calls : List (String × CallDecl)),
    callOpts calls = callOpts d.calls →
    { d with conditions := conds, calls := calls, variables := vars }.toChart = d.toChart :=
  @toChart_decls
example : ∀ (d : Doc), d.wfOK = true → DocWF d := @wfOK_sound

-- Engine protocol (on `Obj`/`J`, never on the text line handled by `Engine.handle`)
example : ∀ (s : Snapshot), SnapOK s → decSnapshot (encSnapshot s) = .ok s :=
  @decSnapshot_encSnapshot
example : ∀ (m : Machine), m.wf = true → namesOK m = true →
    ∀ (s : Snapshot) (ev : Event) (env : HostEnv), SnapOK s →
    (m.step s ev env).1.seq ≤ maxInt → SnapOK (m.step s ev env).1 := @step_snapOK
example : ∀ (m : Machine), m.wf = true → namesOK m = true →
    ∀ (s : Snapshot) (ev : Event) (env : HostEnv), SnapOK s →
    (m.step s ev env).1.seq ≤ maxInt →
    decSnapshot (encSnapshot (m.step s ev env).1) = .ok (m.step s ev env).1 := @reply_readable
example : ∀ (m : Machine) (kvs : Obj) (j : J) (s : Snapshot) (ev : Event) (gs : List (String × Bool)),
    get kvs "op" = some (.str "step") → get kvs "snapshot" = some j →
    decSnapshot j = .ok s → decEvent kvs = .ok ev → decGuards kvs = .ok gs →
    missing m s ev gs = [] → (m.step s ev (envOf gs)).1.seq ≤ maxInt →
    handleObj m kvs = .ok (result ((get kvs "id").getD .null) (m.step s ev (envOf gs)).1
      (m.step s ev (envOf gs)).2 (decide (s.state ∉ m.states))) := @handle_step
example : ∀ (m : Machine) (s : Snapshot) (ev : Event) (gs : List (String × Bool)) (env : HostEnv),
    (∀ n ∈ m.requiredGuards s.state ev.key, envOf gs n = env n) →
    m.step s ev (envOf gs) = m.step s ev env := @guards_irrelevant

-- the fast compiler and check equal the specification, `need` is sufficient
example : ∀ (c : Chart), c.compile = c.compileSpec := @Chart.compile_eq_spec
example : ∀ (c : Chart), c.compile.wf = c.wfDirect := @Chart.wfDirect_eq
example : ∀ (c : Chart), c.lint = [] → c.compile.wf = true := @Chart.lint_nil_wf
example : ∀ (m : Machine), m.wfFast = m.wf := @Machine.wfFast_eq
example : ∀ (m : Machine) (s : Snapshot) (ev : Event) (gs : List (String × Bool)) (env : HostEnv),
    missing m s ev gs = [] → (∀ n v, givenOf gs n = some v → env n = v) →
    m.step s ev (envOf gs) = m.step s ev env := @missing_nil_step

-- `start` with a first event: the compiled machine is the chart, it is a step from the initial
-- message without entering it, a variant into the initial message enters it once, `need` suffices
example : ∀ (c : Chart), c.compile.wf = true → ∀ (n : Nat) (ev : Event) (env : HostEnv),
    c.compile.startWith n ev env = c.startWith n ev env := @Chart.startWith_correct
example : ∀ (m : Machine) (n : Nat) (e : EventId) (env : HostEnv) (b : FBranch),
    m.initial ∈ m.states → m.choose m.initial (.named e) env = some b → b.target ≠ .stay →
    (∀ a ∈ m.exitOf m.initial, ∃ id, a = .cancelTimer id) →
    ((m.step ⟨m.initial, [], [], n, none, []⟩ (.named e) env).1.withBack none,
      (m.step ⟨m.initial, [], [], n, none, []⟩ (.named e) env).2) =
      ((m.startWith n (.named e) env).1, m.exitOf m.initial ++ (m.startWith n (.named e) env).2) :=
  @Machine.startWith_eq_step
example : ∀ (m : Machine) (n : Nat) (e : EventId) (env : HostEnv) (b : FBranch),
    m.choose m.initial (.named e) env = some b →
    m.resolve ⟨m.initial, [], [], n, none, []⟩ b.target = m.initial →
    m.entryOf m.initial = m.initActions →
    m.startWith n (.named e) env =
      realize ⟨"", [], [], n, none, []⟩ m.initial (b.actions ++ m.initActions) :=
  @Machine.startWith_initial
example : ∀ (m : Machine) (n : Nat) (e : EventId) (gs : List (String × Bool)) (env : HostEnv),
    missingStart m e gs = [] → (∀ g v, givenOf gs g = some v → env g = v) →
    m.startWith n (.named e) (envOf gs) = m.startWith n (.named e) env := @missingStart_nil
-- a step from a snapshot whose message no longer exists enters the bot again with the event
example : ∀ (m : Machine) (s : Snapshot) (ev : Event) (env : HostEnv), s.state ∉ m.states →
    m.step s ev env = m.enterBot s (s.timers.map (fun p => .cancelTimer p.1)) ev env :=
  @Machine.step_recover

-- "back to the previous section": a move across a section boundary remembers the message left,
-- "back" leads there and back again, the place is always a message the user left, entering
-- the bot keeps it, and the compiled machine compares the sections of the chart
example : ∀ (m : Machine) (s : Snapshot) (b : FBranch), (m.fire s b).1.back =
    if m.zoneOf s.state = m.zoneOf (m.resolve s b.target) then s.back else some s.state :=
  @Machine.fire_back
example : ∀ (m : Machine) (s : Snapshot) (b : FBranch) (fb : Target) (v : NodeId),
    b.target = .back fb → s.back = some v → v ∈ m.states → m.zoneOf s.state ≠ m.zoneOf v →
    (m.fire s b).1.state = v ∧ (m.fire s b).1.back = some s.state := @Machine.fire_back_swap
example : ∀ (m : Machine) (s : Snapshot) (ev : Event) (env : HostEnv),
    (m.step s ev env).1.back = s.back ∨
      (s.state ∈ m.states ∧ (m.step s ev env).1.back = some s.state) := @Machine.step_back
example : ∀ (m : Machine) (s : Snapshot) (pre : List Action) (ev : Event) (env : HostEnv),
    (m.enterBot s pre ev env).1.back = s.back := @Machine.enterBot_back
example : ∀ (c : Chart) {L : NodeId}, L ∈ c.leafIds → c.compile.zoneOf L = c.parent L :=
  @Chart.compile_zoneOf

-- linear-time execution: clearing the snapshot's timers at once computes exactly `realize`
example : ∀ (s : Snapshot) (T : NodeId) (pre acts : List Action),
    (∀ a ∈ pre, a.isCancel = true) → (∀ p ∈ s.timers, Action.cancelTimer p.1 ∈ pre) →
    realize s T (pre ++ acts) = realizeClear s T pre acts := @Machine.realize_clear
example : ∀ (m : Machine) (s : Snapshot) (b : FBranch), m.fire s b =
    realize (m.markBack s (m.resolve s b.target)) (m.resolve s b.target)
      (m.exitOf s.state ++ staleCancels (m.exitOf s.state) s ++ b.actions ++
        m.entryOf (m.resolve s b.target)) := @Machine.fire_eq_realize
example : ∀ (m : Machine) (s : Snapshot), m.recover s =
    realize s m.initial (s.timers.map (fun p => .cancelTimer p.1) ++ m.initActions) :=
  @Machine.recover_eq_realize
example : ∀ (m : Machine) (s : Snapshot) (pre : List Action) (ev : Event) (env : HostEnv)
    (b : FBranch), s.accepts ev = true → m.choose m.initial ev env = some b →
    m.enterBot s pre ev env =
      realize (s.settle ev) (m.resolve { s with state := m.initial } b.target)
        (pre ++ b.actions ++ m.entryOf (m.resolve { s with state := m.initial } b.target)) :=
  @Machine.enterBot_eq_realize_some
example : ∀ (m : Machine) (s : Snapshot) (pre : List Action) (ev : Event) (env : HostEnv),
    s.accepts ev = false ∨ m.choose m.initial ev env = none →
    m.enterBot s pre ev env = realize (s.settle ev) m.initial (pre ++ m.initActions) :=
  @Machine.enterBot_eq_realize_none

-- actions with outcomes: an event the snapshot does not accept changes nothing, an accepted one
-- without an arrow only removes its wait; the waits after a step are the old ones but the one
-- answered and the step's own (its token is the new counter); a step keeps every other wait
-- whatever the move, unless it calls the same action again answered only at its latest call,
-- which then replaces the older waits
example : ∀ (m : Machine) (s : Snapshot) (ev : Event) (env : HostEnv),
    s.state ∈ m.states → s.accepts ev = false → m.step s ev env = (s, []) := @Machine.step_rejected
example : ∀ (m : Machine) (s : Snapshot) (ev : Event) (env : HostEnv),
    s.state ∈ m.states → m.row s.state ev.key = none → m.step s ev env = (s.settle ev, []) :=
  @Machine.step_no_arrow
example : ∀ (m : Machine) (s : Snapshot) (ev : Event) (env : HostEnv) (p : String × Nat),
    p ∈ (m.step s ev env).1.pending → p ∈ (s.settle ev).pending ∨ p.2 = s.seq + 1 :=
  fun m s ev env _ => Machine.step_pending_new m s ev env
example : ∀ (m : Machine) (s : Snapshot) (ev : Event) (env : HostEnv) (p : String × Nat),
    p ∈ (s.settle ev).pending → p.2 ≠ s.seq + 1 →
    (∀ d t, Action.call p.1 d .latest t ∉ (m.step s ev env).2) →
    p ∈ (m.step s ev env).1.pending :=
  fun m s ev env _ => Machine.step_keeps_wait m s ev env
example : ∀ (m : Machine) (s : Snapshot) (ev : Event) (env : HostEnv) (n : String) (d : Bool)
    (w : Wait) (t : Nat), Action.call n d w t ∈ (m.step s ev env).2 →
    t = (m.step s ev env).1.seq ∧ (w ≠ .free → (n, t) ∈ (m.step s ev env).1.pending) :=
  fun m s ev env _ _ _ _ => Machine.step_records_wait m s ev env
example : ∀ (m : Machine) (s : Snapshot) (ev : Event) (env : HostEnv) (n : String) (d : Bool)
    (t t' : Nat), Action.call n d .latest t ∈ (m.step s ev env).2 →
    (n, t') ∈ (m.step s ev env).1.pending → t' = t :=
  fun m s ev env _ _ _ _ h h' => Machine.step_latest_replaces m s ev env h h'
-- each call is answered once: tokens are fresh and a second outcome of a call is ignored
example : ∀ (m : Machine) (s : Snapshot) (ev : Event) (env : HostEnv),
    s.Fresh → (m.step s ev env).1.Fresh := @Machine.step_fresh
example : ∀ (m : Machine) (n : Nat) (ev : Event) (env : HostEnv),
    (m.startWith n ev env).1.Fresh := @Machine.startWith_fresh
example : ∀ (s : Snapshot) (c o : String) (tok : Nat), s.Fresh →
    s.accepts (.outcome c o tok) = true → tok ≤ s.seq :=
  fun _ _ _ _ hf ha => Snapshot.Fresh.accepts_le hf ha
example : ∀ (m : Machine), m.wf = true → ∀ (s : Snapshot) (c o o' : String) (tok : Nat)
    (env env' : HostEnv), tok ≤ s.seq →
    m.step (m.step s (.outcome c o tok) env).1 (.outcome c o' tok) env' =
      ((m.step s (.outcome c o tok) env).1, []) := @Machine.step_outcome_once
example : ∀ (m : Machine) (n : Nat) (c o : String) (t : Nat) (env : HostEnv),
    m.startWith n (.outcome c o t) env = realize ⟨"", [], [], n, none, []⟩ m.initial m.initActions :=
  @Machine.startWith_outcome

-- variants without a target: the actions without a move, the timer that fired consumed; the
-- message, the places, the place to go back to and the other timers stay; on entering the bot
-- such a variant leads to the initial message
example : ∀ (m : Machine) (s : Snapshot) (ev : Event) (env : HostEnv) (r : Row) (b : FBranch),
    s.state ∈ m.states → s.accepts ev = true → ownsTimer (m.exitOf s.state) ev = true →
    m.row s.state ev.key = some r → r.branches.find? (fun b => guardOK env b.guard) = some b →
    b.target = .stay → m.step s ev env = realize ((s.settle ev).consume ev) s.state b.actions :=
  @Machine.step_stay
example : ∀ (s : Snapshot) (ev : Event) (acts : List Action),
    (∀ a ∈ acts, (∃ n d w t, a = .call n d w t) ∨ ∃ n, a = .send n) →
    (stay s ev acts).1.state = s.state ∧ (stay s ev acts).1.resume = s.resume ∧
      (stay s ev acts).1.back = s.back ∧ (stay s ev acts).1.timers = (s.consume ev).timers ∧
      (stay s ev acts).1.seq = s.seq + 1 := @Machine.stay_keeps
example : ∀ (m : Machine) (s : Snapshot) (pre : List Action) (ev : Event) (env : HostEnv)
    (b : FBranch), s.accepts ev = true → m.choose m.initial ev env = some b → b.target = .stay →
    m.enterBot s pre ev env = realize (s.settle ev) m.initial (pre ++ b.actions ++ m.entryOf m.initial) :=
  @Machine.enterBot_stay

-- background actions do not affect the machine: every action in the foreground gives the same
-- snapshots and the same reply up to the flag
example : ∀ (m : Machine) (s : Snapshot) (ev : Event) (env : HostEnv),
    m.undetach.step s ev env = ((m.step s ev env).1, (m.step s ev env).2.map Action.undetach) :=
  @Machine.step_undetach
example : ∀ (m : Machine) (n : Nat) (ev : Event) (env : HostEnv),
    m.undetach.startWith n ev env =
      ((m.startWith n ev env).1, (m.startWith n ev env).2.map Action.undetach) :=
  @Machine.startWith_undetach
example : ∀ (m : Machine) (s : Snapshot),
    m.undetach.recover s = ((m.recover s).1, (m.recover s).2.map Action.undetach) :=
  @Machine.recover_undetach

-- the protocol: the arrow is looked up by exactly the event sent, an outcome event of an action
-- is decoded as that outcome, `start` takes neither timer nor outcome events, `restart` is
-- `recover` without the places, the place to go back to and the waits, and clears the waits
example : ∀ (c o : String), c ≠ "" → o ≠ "" → ':' ∉ o.toList →
    splitOutcome (c ++ ":" ++ o) = some (c, o) := @splitOutcome_outcomeEvent
example : ∀ (kvs : Obj) (ev : Event), decEvent kvs = .ok ev → get kvs "event" = some (.str ev.key) :=
  @decEvent_key
example : ∀ (kvs : Obj) (c o : String) (tok : Nat), c ≠ "" → o ≠ "" → ':' ∉ o.toList →
    tok ≤ maxInt → get kvs "event" = some (.str (outcomeEvent c o)) →
    get kvs "token" = some (.num tok 0) → decEvent kvs = .ok (.outcome c o tok) := @decEvent_outcome
example : ∀ (kvs : Obj) (e : EventId), decStartEvent kvs = .ok e →
    get kvs "event" = some (.str e) ∧ e ≠ "" ∧ e.startsWith "timer:" = false ∧
      e.startsWith "call:" = false := @decStartEvent_ok
example : ∀ (m : Machine) (kvs : Obj) (j : J) (s : Snapshot),
    get kvs "op" = some (.str "restart") → get kvs "snapshot" = some j → decSnapshot j = .ok s →
    (m.recover { s with resume := [], back := none, pending := [] }).1.seq ≤ maxInt →
    handleObj m kvs = .ok (result ((get kvs "id").getD .null)
      (m.recover { s with resume := [], back := none, pending := [] }).1
      (m.recover { s with resume := [], back := none, pending := [] }).2 false) := @handle_restart
example : ∀ (m : Machine) (s : Snapshot) (p : String × Nat),
    p ∈ (m.recover { s with resume := [], back := none, pending := [] }).1.pending →
    p.2 = (m.recover { s with resume := [], back := none, pending := [] }).1.seq :=
  fun m s _ h => restart_pending m s h
example : ∀ (m : Machine) (s : Snapshot), s.Fresh →
    (m.recover { s with resume := [], back := none, pending := [] }).1.Fresh := @restart_fresh

-- the first entry without a variant; no duplicate waits; waits survive a document update;
-- a timer consumed without a move; tokens grow; each call is answered once for good
example : ∀ (m : Machine) (n : Nat) (ev : Event) (env : HostEnv), m.choose m.initial ev env = none →
    m.startWith n ev env = realize ⟨"", [], [], n, none, []⟩ m.initial m.initActions :=
  @Machine.startWith_unmatched
example : ∀ (m : Machine) (s : Snapshot) (ev : Event) (env : HostEnv),
    s.pending.Nodup → (m.step s ev env).1.pending.Nodup := @Machine.step_nodup
example : ∀ (m : Machine) (n : Nat) (ev : Event) (env : HostEnv),
    (m.startWith n ev env).1.pending.Nodup := @Machine.startWith_nodup
example : ∀ (m : Machine) (s : Snapshot) (inp : List Input),
    s.pending.Nodup → (m.run s inp).1.pending.Nodup := @Machine.run_nodup
example : ∀ (m : Machine) (s : Snapshot) (c o : String) (tok : Nat) (env : HostEnv),
    (c, tok) ∈ s.pending → tok ≤ s.seq →
    (c, tok) ∉ (m.step s (.outcome c o tok) env).1.pending ∧
      m.step s (.outcome c o tok) env ≠ (s, []) := @Machine.step_outcome_accepted
example : ∀ (m : Machine) (s : Snapshot) (id : String) (tok : Nat) (env env' : HostEnv) (r : Row)
    (b : FBranch), s.Fresh → s.state ∈ m.states → s.accepts (.timer id tok) = true →
    ownsTimer (m.exitOf s.state) (.timer id tok) = true → m.row s.state (Event.timer id tok).key = some r →
    r.branches.find? (fun b => guardOK env b.guard) = some b → b.target = .stay →
    m.step (m.step s (.timer id tok) env).1 (.timer id tok) env' =
      ((m.step s (.timer id tok) env).1, []) := @Machine.step_stay_timer_once
example : ∀ (m : Machine) (s : Snapshot) (ev : Event) (env : HostEnv),
    ((m.step s ev env).1.seq = s.seq ∧ (m.step s ev env).2 = []) ∨
      (m.step s ev env).1.seq = s.seq + 1 := @Machine.step_seq
example : ∀ (m : Machine) (s : Snapshot) (ev ev' : Event) (env env' : HostEnv) (n n' : String)
    (d d' : Bool) (w w' : Wait) (t t' : Nat), Action.call n d w t ∈ (m.step s ev env).2 →
    Action.call n' d' w' t' ∈ (m.step (m.step s ev env).1 ev' env').2 → t' = t + 1 :=
  fun m s ev ev' env env' _ _ _ _ _ _ _ _ h1 h2 => Machine.consecutive_tokens m s ev ev' env env' h1 h2
example : ∀ (m : Machine), m.wf = true → ∀ (s : Snapshot) (c o o' : String) (tok : Nat)
    (env env' : HostEnv) (inp : List Input), tok ≤ s.seq →
    m.step (m.run (m.step s (.outcome c o tok) env).1 inp).1 (.outcome c o' tok) env' =
      ((m.run (m.step s (.outcome c o tok) env).1 inp).1, []) := @Machine.outcome_once_forever
example : ∀ (m : Machine) (n : Nat), (startAt m n).1.Fresh := @startAt_fresh

-- the reference semantics: an accepted outcome without a matching variant only loses its wait;
-- V6 after `check`, and then one call of an action with outcomes per step; V5 after `check`;
-- `restart` of a compiled machine leaves no waits; a document's variants only call actions, and
-- its variants without a target move nothing; named events never have reserved keys
example : ∀ (c : Chart) (s : Snapshot) (a o : String) (tok : Nat) (env : HostEnv),
    s.state ∈ c.leafIds → (a, tok) ∈ s.pending →
    (c.cands s.state (outcomeEvent a o)).find? (fun b => guardOK env b.guard) = none →
    c.step s (.outcome a o tok) env =
      ({ s with pending := s.pending.filter (fun p => decide (p ≠ (a, tok))) }, []) :=
  @Chart.step_outcome_unmatched
example : ∀ (c : Chart), c.WaitsOnce → ∀ (s : Snapshot) (ev : Event) (env : HostEnv),
    (waitNames (c.step s ev env).2).Nodup := @Chart.step_waits_once
example : ∀ (c : Chart), c.compile.wf = true → c.WaitsOnce → ∀ (s : Snapshot) (ev : Event)
    (env : HostEnv), (waitNames (c.compile.step s ev env).2).Nodup := @Chart.compile_step_waits_once
example : ∀ (c : Chart) (s : Snapshot),
    (c.compile.recover { s with resume := [], back := none, pending := [] }).1.pending = [] :=
  @Chart.restart_no_waits
example : ∀ (d : Doc), d.check = [] → d.toChart.WaitsOnce := @check_waitsOnce
example : ∀ (d : Doc), d.check = [] → ∀ t ∈ d.toChart.transitions, t.event.startsWith "call:" = true →
    ∃ n o c, splitOutcome (t.event.drop 5).toString = some (n, o) ∧ lookup d.calls n = some c ∧
      c.outcomes ≠ [] ∧ (o = "failed" ∨ o ∈ c.outcomes) := @check_outcome_events
example : ∀ (d : Doc), d.check = [] → ∀ (s : Snapshot) (ev : Event) (env : HostEnv),
    (waitNames (d.toChart.compile.step s ev env).2).Nodup := @check_step_waits_once
example : ∀ (d : Doc), ∀ t ∈ d.toChart.transitions, ∀ b ∈ t.branches, ∀ a ∈ b.actions,
    ∃ n dt w tok, a = Action.call n dt w tok := @toChart_calls
example : ∀ (d : Doc), d.toChart.compile.wf = true → ∀ (s : Snapshot) (ev : Event) (env : HostEnv),
    s.state ∈ d.toChart.leafIds → s.accepts ev = true →
    ownsTimer (d.toChart.exitActs s.state) ev = true → ∀ (b : Branch),
    (d.toChart.cands s.state ev.key).find? (fun b => guardOK env b.guard) = some b →
    b.target = .stay →
    (d.toChart.compile.step s ev env).1.state = s.state ∧
      (d.toChart.compile.step s ev env).1.resume = s.resume ∧
      (d.toChart.compile.step s ev env).1.back = s.back ∧
      (d.toChart.compile.step s ev env).1.timers = (s.consume ev).timers ∧
      (d.toChart.compile.step s ev env).1.seq = s.seq + 1 ∧
      ∀ a ∈ (d.toChart.compile.step s ev env).2, ∃ n dt w tok, a = Action.call n dt w tok :=
  @doc_stay
example : ∀ (kvs : Obj) (n : String), decEvent kvs = .ok (.named n) → ∀ (c o id : String),
    n ≠ outcomeEvent c o ∧ n ≠ timerEvent id := fun _ _ h => decEvent_named_key h

-- Which of the referenced functions are opaque to the logic (partial defs):
#print Figram.Format.parse
#print Figram.Format.parseValue
#print Figram.Format.render
#print Figram.Format.renderCompact
#print Figram.Format.nodeErrors
#print Figram.Format.J.keysUnique
#print Figram.Engine.handle

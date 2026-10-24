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
example : ∀ (d : Doc) (conds calls vars : List (String × String)),
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
    m.initial ∈ m.states → m.choose m.initial (.named e) env = some b →
    (∀ a ∈ m.exitOf m.initial, ∃ id, a = .cancelTimer id) →
    ((m.step ⟨m.initial, [], [], n, none⟩ (.named e) env).1.withBack none,
      (m.step ⟨m.initial, [], [], n, none⟩ (.named e) env).2) =
      ((m.startWith n (.named e) env).1, m.exitOf m.initial ++ (m.startWith n (.named e) env).2) :=
  @Machine.startWith_eq_step
example : ∀ (m : Machine) (n : Nat) (ev : Event) (env : HostEnv) (b : FBranch),
    m.choose m.initial ev env = some b → m.resolve ⟨"", [], [], n, none⟩ b.target = m.initial →
    m.entryOf m.initial = m.initActions →
    m.startWith n ev env = realize ⟨"", [], [], n, none⟩ m.initial (b.actions ++ m.initActions) :=
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

-- Which of the referenced functions are opaque to the logic (partial defs):
#print Figram.Format.parse
#print Figram.Format.parseValue
#print Figram.Format.render
#print Figram.Format.renderCompact
#print Figram.Format.nodeErrors
#print Figram.Format.J.keysUnique
#print Figram.Engine.handle

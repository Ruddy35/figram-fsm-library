/-
Pinned statements of every theorem named in README.md / AUDIT.md / FORMAT.md.
Each `example` re-states the theorem in full (as the auditor reads it) and is
closed by the theorem itself: if a theorem is weakened, renamed or its
hypotheses change, this file stops compiling. Run: lake env lean audit/proofs/Statements.lean
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

-- Which of the referenced functions are opaque to the logic (partial defs):
#print Figram.Format.parse
#print Figram.Format.parseValue
#print Figram.Format.render
#print Figram.Format.renderCompact
#print Figram.Format.nodeErrors
#print Figram.Format.J.keysUnique
#print Figram.Engine.handle

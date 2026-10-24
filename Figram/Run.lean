import Figram.Correct

/-!
# Scenarios: sequences of events

On **any** scenario from **any** snapshot:

* `run_correct` — the compiled machine and the designer's chart produce
  the same snapshots and the same actions;
* `chart_run_valid` — after the first event the user is on an existing
  message, even if the initial snapshot is stale.
-/

namespace Figram

/-- One event of a scenario together with the values of the developer's conditions. -/
abbrev Input := Event × HostEnv

def Machine.run (m : Machine) (s : Snapshot) : List Input → Snapshot × List Action
  | [] => (s, [])
  | (e, env) :: rest =>
    let (s', a) := m.step s e env
    let (s'', as) := m.run s' rest
    (s'', a ++ as)

def Chart.run (c : Chart) (s : Snapshot) : List Input → Snapshot × List Action
  | [] => (s, [])
  | (e, env) :: rest =>
    let (s', a) := c.step s e env
    let (s'', as) := c.run s' rest
    (s'', a ++ as)

theorem Machine.run_state_valid (m : Machine) (hwf : m.wf = true)
    (s : Snapshot) (x : Input) (rest : List Input) :
    (m.run s (x :: rest)).1.state ∈ m.states := by
  induction rest generalizing s x with
  | nil =>
    obtain ⟨e, env⟩ := x
    simpa [Machine.run] using m.step_state_valid hwf s e env
  | cons y rest ih =>
    obtain ⟨e, env⟩ := x
    exact ih _ y

namespace Chart

theorem run_correct (c : Chart) (hwf : c.compile.wf = true)
    (s : Snapshot) (inp : List Input) : c.compile.run s inp = c.run s inp := by
  induction inp generalizing s with
  | nil => rfl
  | cons x rest ih =>
    obtain ⟨e, env⟩ := x
    simp only [Machine.run, Chart.run]
    rw [c.compile_correct hwf s e env, ih]

/-- The user never gets "lost": after any non-empty scenario from any
snapshot the user is on a message of the chart. -/
theorem chart_run_valid (c : Chart) (hwf : c.compile.wf = true)
    (s : Snapshot) (x : Input) (rest : List Input) :
    (c.run s (x :: rest)).1.state ∈ c.leafIds := by
  rw [← c.run_correct hwf s]
  exact c.compile.run_state_valid hwf s x rest

/-- A full session from the first entry. -/
theorem session_correct (c : Chart) (hwf : c.compile.wf = true) (inp : List Input) :
    c.compile.run c.compile.start.1 inp = c.run c.start.1 inp := by
  rw [c.start_correct]
  exact c.run_correct hwf _ inp

/-- A full session from the first entry with a first event. -/
theorem sessionWith_correct (c : Chart) (hwf : c.compile.wf = true) (n : Nat) (ev : Event)
    (env : HostEnv) (inp : List Input) :
    c.compile.run (c.compile.startWith n ev env).1 inp = c.run (c.startWith n ev env).1 inp := by
  rw [c.startWith_correct hwf]
  exact c.run_correct hwf _ inp

end Chart
end Figram

import Figram.Correct

/-!
# Сценарии: последовательности событий

На **любом** сценарии из **любого** снимка:

* `run_correct` — скомпилированный автомат и схема дизайнера выдают
  одинаковые снимки и одинаковые действия;
* `chart_run_valid` — после первого события пользователь стоит на
  существующем сообщении, даже если исходный снимок устарел.
-/

namespace Figram

/-- Одно событие сценария вместе со значениями условий разработчика. -/
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

/-- Пользователь никогда не «теряется»: после любого непустого сценария
из любого снимка он стоит на сообщении схемы. -/
theorem chart_run_valid (c : Chart) (hwf : c.compile.wf = true)
    (s : Snapshot) (x : Input) (rest : List Input) :
    (c.run s (x :: rest)).1.state ∈ c.leafIds := by
  rw [← c.run_correct hwf s]
  exact c.compile.run_state_valid hwf s x rest

/-- Полная сессия с первого входа. -/
theorem session_correct (c : Chart) (hwf : c.compile.wf = true) (inp : List Input) :
    c.compile.run c.compile.start.1 inp = c.run c.start.1 inp := by
  rw [c.start_correct]
  exact c.run_correct hwf _ inp

end Chart
end Figram

import Figram.Core

/-!
# Свойства ядра

1. `step_state_valid` — после любого шага из **любого** снимка пользователь
   стоит на существующем сообщении (если автомат прошёл проверку `wf`).
   Снимок с удалённым сообщением восстанавливается (`recover`).
2. `start_state_valid` — то же для первого входа.
3. `step_guard_local` — шаг зависит только от условий из `requiredGuards`.
-/

namespace Figram

theorem realizeOne_state (tok : Nat) (acc : Snapshot × List Action) (a : Action) :
    (realizeOne tok acc a).1.state = acc.1.state := by
  cases a <;> rfl

theorem foldl_realize_state (tok : Nat) (acts : List Action) (acc : Snapshot × List Action) :
    (acts.foldl (realizeOne tok) acc).1.state = acc.1.state := by
  induction acts generalizing acc with
  | nil => rfl
  | cons a as ih => simp only [List.foldl_cons]; rw [ih, realizeOne_state]

theorem realize_state (s : Snapshot) (T : NodeId) (acts : List Action) :
    (realize s T acts).1.state = T := by
  simp only [realize, foldl_realize_state]

theorem lookup_some_mem {α β} [DecidableEq α] {t : List (α × β)} {k : α} {v : β}
    (h : lookup t k = some v) : (k, v) ∈ t := by
  simp only [lookup, Option.map_eq_some_iff] at h
  obtain ⟨p, hp, rfl⟩ := h
  have hk := List.find?_some hp
  simp only [decide_eq_true_eq] at hk
  rw [← hk]
  exact List.mem_of_find?_eq_some hp

/-- Выбор варианта не зависит от вариантов после первого безусловного. -/
theorem find?_untilUnguarded (env : HostEnv) (bs : List FBranch) :
    bs.find? (fun b => guardOK env b.guard) =
      (untilUnguarded (·.guard) bs).find? (fun b => guardOK env b.guard) := by
  induction bs with
  | nil => rfl
  | cons b bs ih =>
    cases hg : b.guard with
    | none => simp [untilUnguarded, hg, guardOK]
    | some n =>
      simp only [untilUnguarded, hg, List.find?_cons, guardOK]
      cases hn : env n
      · exact ih
      · rfl

theorem neededIn_given {known : String → Option Bool} {bs : List FBranch} {n : String}
    (h : n ∈ Machine.neededIn known bs) : known n = none := by
  induction bs with
  | nil => simp [Machine.neededIn] at h
  | cons b bs ih =>
    unfold Machine.neededIn at h
    split at h
    · simp at h
    · split at h
      · simp at h
      · exact ih h
      · rename_i g _ hg
        rcases List.mem_cons.mp h with rfl | h
        · exact hg
        · exact ih h

/-- Если недостающих условий нет, выбор варианта одинаков для любых значений,
согласных с известными. -/
theorem find?_neededIn {known : String → Option Bool} {bs : List FBranch} (env₁ env₂ : HostEnv)
    (h : Machine.neededIn known bs = [])
    (h₁ : ∀ n v, known n = some v → env₁ n = v) (h₂ : ∀ n v, known n = some v → env₂ n = v) :
    bs.find? (fun b => guardOK env₁ b.guard) = bs.find? (fun b => guardOK env₂ b.guard) := by
  induction bs with
  | nil => rfl
  | cons b bs ih =>
    unfold Machine.neededIn at h
    cases hg : b.guard with
    | none => simp [hg, guardOK]
    | some g =>
      simp only [hg] at h
      cases hv : known g with
      | none => simp [hv] at h
      | some v =>
        simp only [hv] at h
        have e₁ := h₁ g v hv
        have e₂ := h₂ g v hv
        cases v with
        | true => simp [hg, guardOK, e₁, e₂]
        | false =>
          simp only [List.find?_cons, hg, guardOK, e₁, e₂]
          exact ih h

namespace Machine

/-- Найденная стрелка — одна из стрелок автомата. -/
theorem row_mem {m : Machine} {s : NodeId} {e : EventId} {r : Row} (h : m.row s e = some r) :
    r ∈ m.rows := by
  unfold row at h
  have hr := List.mem_of_find?_eq_some h
  cases hl : lookup m.table s with
  | none => simp [hl] at hr
  | some rs =>
    rw [hl] at hr
    simp only [rows, List.mem_flatMap]
    exact ⟨(s, rs), lookup_some_mem hl, hr⟩

theorem wf_parts {m : Machine} (h : m.wf = true) :
    m.initial ∈ m.states ∧
    (∀ r ∈ m.rows, ∀ b ∈ r.branches, m.targetOK b.target = true) ∧
    (∀ r ∈ m.resumes, r.default ∈ m.states ∧ ∀ v ∈ r.members, v ∈ m.states) := by
  simp only [wf, Bool.and_eq_true, decide_eq_true_eq, List.all_eq_true] at h
  exact ⟨h.1.1, h.1.2, fun r hr => ⟨(h.2 r hr).1, fun v hv => (h.2 r hr).2 v hv⟩⟩

theorem resolve_mem {m : Machine} (hwf : m.wf = true) (s : Snapshot) (t : Target)
    (ht : m.targetOK t = true) : m.resolve s t ∈ m.states := by
  obtain ⟨_, _, hres⟩ := wf_parts hwf
  cases t with
  | node n => simpa [targetOK, resolve] using ht
  | resume sec =>
    simp only [resolve]
    cases hf : m.resumes.find? (fun r => decide (r.sec = sec)) with
    | none =>
      simp only [targetOK, List.any_eq_true, decide_eq_true_eq] at ht
      obtain ⟨r, hr, hsec⟩ := ht
      have := List.find?_eq_none.mp hf r hr
      simp [hsec] at this
    | some r =>
      have hr := hres r (List.mem_of_find?_eq_some hf)
      simp only
      split
      · split
        · exact hr.2 _ (by assumption)
        · exact hr.1
      · exact hr.1

theorem start_state_valid (m : Machine) (hwf : m.wf = true) : m.start.1.state ∈ m.states := by
  simp only [start, realize_state]
  exact (wf_parts hwf).1

theorem step_state_valid (m : Machine) (hwf : m.wf = true)
    (s : Snapshot) (ev : Event) (env : HostEnv) :
    (m.step s ev env).1.state ∈ m.states := by
  obtain ⟨hinit, hrows, _⟩ := wf_parts hwf
  unfold step
  split
  · simp only [recover, realize_state]; exact hinit
  · rename_i hs
    have hs : s.state ∈ m.states := by simpa using hs
    split
    · exact hs
    · split
      · exact hs
      · rename_i r hr
        split
        · exact hs
        · rename_i b hb
          simp only [fire, realize_state]
          exact resolve_mem hwf s _ (hrows r (row_mem hr) b
            (List.mem_of_find?_eq_some hb))

theorem find?_congr {α} (l : List α) (p q : α → Bool)
    (h : ∀ x ∈ l, p x = q x) : l.find? p = l.find? q := by
  induction l with
  | nil => rfl
  | cons x xs ih =>
    have hx := h x (by simp)
    have hxs : ∀ y ∈ xs, p y = q y := fun y hy => h y (by simp [hy])
    simp only [List.find?_cons, hx, ih hxs]

theorem step_guard_local (m : Machine) (s : Snapshot) (ev : Event) (env₁ env₂ : HostEnv)
    (h : ∀ n ∈ m.requiredGuards s.state ev.key, env₁ n = env₂ n) :
    m.step s ev env₁ = m.step s ev env₂ := by
  unfold step
  unfold requiredGuards at h
  split
  · rfl
  · split
    · rfl
    · split
      · rfl
      · rename_i r hr
        simp only [hr] at h
        have hfind : (untilUnguarded (·.guard) r.branches).find? (fun b => guardOK env₁ b.guard)
            = (untilUnguarded (·.guard) r.branches).find? (fun b => guardOK env₂ b.guard) := by
          apply find?_congr
          intro b hb
          cases hg : b.guard with
          | none => rfl
          | some n =>
            simp only [guardOK]
            exact h n (List.mem_filterMap.mpr ⟨b, hb, hg⟩)
        rw [find?_untilUnguarded env₁, find?_untilUnguarded env₂, hfind]

end Machine
end Figram

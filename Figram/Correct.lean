import Figram.Compile
import Figram.CoreProofs

/-!
# Главная теорема: компилятор сохраняет поведение

`compile_correct`: если скомпилированный автомат прошёл проверку `wf`, то для
**любого** снимка, любого события и любых значений условий разработчика шаг
скомпилированного автомата в точности равен шагу схемы по эталонной
семантике: тот же новый снимок и тот же список действий.

`start_correct`: первый вход тоже совпадает.
-/

namespace Figram

/-- Поиск по таблице, построенной по списку ключей. -/
theorem lookup_map {β} (keys : List NodeId) (f : NodeId → β) (k : NodeId) (hk : k ∈ keys) :
    lookup (keys.map fun x => (x, f x)) k = some (f k) := by
  induction keys with
  | nil => simp at hk
  | cons x xs ih =>
    by_cases hx : x = k
    · subst hx; simp [lookup]
    · have : k ∈ xs := by
        rcases List.mem_cons.mp hk with h | h
        · exact absurd h.symm hx
        · exact h
      have := ih this
      simp only [lookup, List.map_cons, List.find?_cons, hx, decide_false] at this ⊢
      exact this

theorem find?_eq_self {α} [DecidableEq α] (l : List α) (a : α) (h : a ∈ l) :
    l.find? (fun x => decide (x = a)) = some a := by
  induction l with
  | nil => simp at h
  | cons x xs ih =>
    rw [List.find?_cons]
    by_cases hx : x = a
    · subst hx; simp
    · have : a ∈ xs := by
        rcases List.mem_cons.mp h with h | h
        · exact absurd h.symm hx
        · exact h
      simp [hx, ih this]

namespace Chart

variable (c : Chart)

theorem mem_cands {L : NodeId} {e : EventId} {b : Branch} (hb : b ∈ c.cands L e) :
    ∃ t ∈ c.transitions, t.event = e ∧ b ∈ t.branches := by
  simp only [cands, branchesAt, List.mem_flatMap, List.mem_filter, decide_eq_true_eq] at hb
  obtain ⟨_, _, t, ⟨ht, _, he⟩, hbt⟩ := hb
  exact ⟨t, ht, he, hbt⟩

theorem hist_mem {L : NodeId} {e : EventId} {b : Branch} {s : NodeId}
    (hb : b ∈ c.cands L e) (ht : b.target = .history s) : s ∈ dedup c.histSecs := by
  obtain ⟨t, htr, _, hbt⟩ := c.mem_cands hb
  rw [mem_dedup]
  simp only [histSecs, List.mem_flatMap, List.mem_filterMap]
  exact ⟨t, htr, b, hbt, by simp [ht]⟩

theorem row_mem {L : NodeId} {e : EventId} {b : Branch} (hL : L ∈ c.leafIds)
    (hb : b ∈ c.cands L e) : c.compileRow L e ∈ c.compile.rows := by
  obtain ⟨t, htr, he, _⟩ := c.mem_cands hb
  have hev : e ∈ c.events := by
    simp only [events, mem_dedup, List.mem_map]
    exact ⟨t, htr, he⟩
  simp only [compile, List.mem_flatMap, List.mem_filter, List.mem_map]
  refine ⟨L, hL, ⟨e, hev, rfl⟩, ?_⟩
  simp only [compileRow, List.isEmpty_map, Bool.not_eq_true']
  cases h : (c.cands L e).isEmpty with
  | false => rfl
  | true => simp [List.isEmpty_iff] at h; simp [h] at hb

theorem row_some {L : NodeId} {e : EventId} {r : Row}
    (h : c.compile.row L e = some r) : r = c.compileRow L e := by
  have hp : r.src = L ∧ r.event = e := by simpa using List.find?_some h
  have hm := List.mem_of_find?_eq_some h
  simp only [compile, List.mem_flatMap, List.mem_filter, List.mem_map] at hm
  obtain ⟨L', _, ⟨e', _, hr⟩, _⟩ := hm
  subst hr
  simp only [compileRow] at hp
  obtain ⟨rfl, rfl⟩ := hp
  rfl

theorem row_none {L : NodeId} {e : EventId} (hL : L ∈ c.leafIds)
    (h : c.compile.row L e = none) : c.cands L e = [] := by
  apply List.eq_nil_iff_forall_not_mem.mpr
  intro b hb
  have := List.find?_eq_none.mp h _ (c.row_mem hL hb)
  simp [compileRow] at this

theorem resolve_eq (s : Snapshot) {L : NodeId} {e : EventId} {b : Branch}
    (hb : b ∈ c.cands L e) : c.compile.resolve s (c.toF b).target = c.resolve s b.target := by
  cases ht : b.target with
  | node n => simp [toF, ht, Machine.resolve, resolve]
  | history s0 =>
    have hm := c.hist_mem hb ht
    have hf : c.compile.resumes.find? (fun r => decide (r.sec = s0)) = some (c.mkResume s0) := by
      simp only [compile, List.find?_map]
      have : ((fun r : ResumeSpec => decide (r.sec = s0)) ∘ c.mkResume) =
          fun x => decide (x = s0) := by funext x; rfl
      rw [this, find?_eq_self _ _ hm]
      rfl
    simp only [toF, ht, Machine.resolve, hf, resolve, restoreTarget, mkResume]
    cases lookup s.resume s0 <;> rfl

/-- Для выбранного варианта шаг скомпилированного автомата совпадает со схемой. -/
theorem fire_eq (hwf : c.compile.wf = true) (s : Snapshot) {e : EventId} {b : Branch}
    (hs : s.state ∈ c.leafIds) (hb : b ∈ c.cands s.state e) :
    c.compile.fire s (c.toF b) = c.fire s b := by
  have hT := c.resolve_eq s hb
  have hrow := c.row_mem hs hb
  have hok : c.compile.targetOK (c.toF b).target = true := by
    obtain ⟨_, hrows, _⟩ := Machine.wf_parts hwf
    exact hrows _ hrow _ (List.mem_map.mpr ⟨b, hb, rfl⟩)
  have hTmem : c.resolve s b.target ∈ c.leafIds := by
    rw [← hT]; exact Machine.resolve_mem hwf s _ hok
  have hex : c.compile.exitOf s.state = c.exitActs s.state := by
    simp only [Machine.exitOf, compile, lookup_map _ _ _ hs, Option.getD_some]
  have hen : c.compile.entryOf (c.resolve s b.target) = c.entryActs (c.resolve s b.target) := by
    simp only [Machine.entryOf, compile, lookup_map _ _ _ hTmem, Option.getD_some]
  simp only [Machine.fire, fire, hT, hex, hen]
  rfl

/-- **Главная теорема.** -/
theorem compile_correct (hwf : c.compile.wf = true) (s : Snapshot) (ev : Event) (env : HostEnv) :
    c.compile.step s ev env = c.step s ev env := by
  unfold Machine.step Chart.step
  by_cases hs : s.state ∈ c.leafIds
  · have hs' : s.state ∈ c.compile.states := hs
    -- Условие приёма события читает действия выхода (`ownsTimer`): у
    -- скомпилированного автомата и у схемы они совпадают.
    have hex : c.compile.exitOf s.state = c.exitActs s.state := by
      simp only [Machine.exitOf, compile, lookup_map _ _ _ hs, Option.getD_some]
    simp only [hs, hs', not_true_eq_false, ite_false, hex]
    split
    · rfl
    · cases hr : c.compile.row s.state ev.key with
      | none => simp [c.row_none hs hr]
      | some r =>
        have hr' := c.row_some hr
        subst hr'
        simp only [compileRow, List.find?_map]
        have hp : ((fun b : FBranch => guardOK env b.guard) ∘ c.toF) =
            fun b => guardOK env b.guard := by funext b; rfl
        rw [hp]
        cases hb : (c.cands s.state ev.key).find? (fun b => guardOK env b.guard) with
        | none => rfl
        | some b =>
          simp only [Option.map_some]
          exact c.fire_eq hwf s hs (List.mem_of_find?_eq_some hb)
  · have hs' : s.state ∉ c.compile.states := hs
    simp only [hs, hs', not_false_eq_true, ite_true]
    rfl

theorem start_correct : c.compile.start = c.start := rfl

end Chart
end Figram

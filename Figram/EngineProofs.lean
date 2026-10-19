import Figram.Engine
import Figram.CoreProofs
import Figram.FormatProofs

/-!
# Свойства протокола движка

1. `decSnapshot_encSnapshot` — снимок, записанный движком, читается обратно
   **в точности** тем же (если он в допустимых пределах `SnapOK`).
2. `step_snapOK` — шаг автомата сохраняет допустимость снимка, если имена
   в автомате непусты (`Machine.namesOK`, проверяется при загрузке) и счётчик
   не переполнен (переполнение движок отвергает ошибкой `seq_overflow`).
   Вместе с (1): движок никогда не выдаёт снимок, который сам не прочитает.
3. `handle_step` — запрос `step` с полным набором условий исполняет ровно
   доказанный `Machine.step`.
-/

namespace Figram.Engine
open Figram.Format

/-- Допустимый снимок — ровно то, что разрешает snapshot.schema.json. -/
structure SnapOK (s : Snapshot) : Prop where
  state : s.state ≠ ""
  resume : ∀ p ∈ s.resume, p.1 ≠ "" ∧ p.2 ≠ ""
  timers : ∀ p ∈ s.timers, p.1 ≠ "" ∧ p.2 ≤ maxInt
  seq : s.seq ≤ maxInt

theorem isEmpty_false {s : String} (h : s ≠ "") : s.isEmpty = false := by
  simpa [String.isEmpty_iff] using h

theorem nonEmpty_str (what s : String) (h : s ≠ "") : nonEmpty what (.str s) = .ok s := by
  simp [nonEmpty, isEmpty_false h]; rfl

theorem boundedNat_num (what : String) (n : Nat) (h : n ≤ maxInt) :
    boundedNat what (.num n 0) = .ok n := by
  simp [boundedNat, numToNat_nat, h]; rfl

theorem mapM_resume (r : List (String × String)) (h : ∀ p ∈ r, p.1 ≠ "" ∧ p.2 ≠ "") :
    (r.map fun p => (p.1, J.str p.2)).mapM decResumeEntry = .ok r := by
  induction r with
  | nil => rfl
  | cons p r ih =>
    have hp := h p (by simp)
    have hr := ih (fun q hq => h q (by simp [hq]))
    simp only [List.map_cons, List.mapM_cons, decResumeEntry, isEmpty_false hp.1,
      nonEmpty_str _ _ hp.2, hr]
    rfl

theorem mapM_timers (t : List (String × Nat)) (h : ∀ p ∈ t, p.1 ≠ "" ∧ p.2 ≤ maxInt) :
    (t.map fun p => (p.1, J.num p.2 0)).mapM decTimerEntry = .ok t := by
  induction t with
  | nil => rfl
  | cons p t ih =>
    have hp := h p (by simp)
    have hr := ih (fun q hq => h q (by simp [hq]))
    simp only [List.map_cons, List.mapM_cons, decTimerEntry, isEmpty_false hp.1,
      boundedNat_num _ _ hp.2, hr]
    rfl

/-- **Снимок, записанный движком, читается обратно в точности тем же.** -/
theorem decSnapshot_encSnapshot (s : Snapshot) (h : SnapOK s) :
    decSnapshot (encSnapshot s) = .ok s := by
  obtain ⟨st, res, tms, seq⟩ := s
  obtain ⟨hst, hres, htms, hseq⟩ := h
  simp only at hst hres htms hseq
  have hkeys : ∀ (A : Obj), (∀ p ∈ A, p.1 ∈ snapshotKeys) → checkKeys A = .ok () := by
    intro A hA
    simp only [checkKeys]
    rw [List.find?_eq_none.mpr (fun p hp => by simp [hA p hp])]
    rfl
  simp only [encSnapshot, decSnapshot]
  rw [hkeys _ (by
    intro p hp
    simp only [List.mem_append, List.mem_singleton] at hp
    rcases hp with ((rfl | hp) | hp) | hp
    · simp [snapshotKeys]
    all_goals (rw [field_keys _ _ _ p hp]; simp [snapshotKeys]))]
  have h1 := mapM_resume _ hres
  have h2 := mapM_timers _ htms
  by_cases hr : res = [] <;> by_cases ht : tms = [] <;> by_cases hz : seq = 0 <;>
    simp [checkVersion, Format.get, field, hr, ht, hz, nonEmpty_str _ _ hst,
      h1, h2, boundedNat_num _ _ hseq, bind, Except.bind, pure, Except.pure] <;>
    (try subst hr) <;> (try subst ht) <;> (try subst hz) <;> rfl

/-! ## Шаг сохраняет допустимость снимка -/

/-- Допустимость мест и таймеров. -/
def Inv (x : Snapshot) : Prop :=
  (∀ p ∈ x.resume, p.1 ≠ "" ∧ p.2 ≠ "") ∧ (∀ p ∈ x.timers, p.1 ≠ "" ∧ p.2 ≤ maxInt)

theorem mem_assign {α β} [DecidableEq α] {t : List (α × β)} {k : α} {v : β} {p : α × β}
    (h : p ∈ assign t k v) : p = (k, v) ∨ p ∈ t := by
  simp only [assign, List.mem_cons, List.mem_filter] at h
  rcases h with h | h
  · exact Or.inl h
  · exact Or.inr h.1

theorem mem_erase {α β} [DecidableEq α] {t : List (α × β)} {k : α} {p : α × β}
    (h : p ∈ erase t k) : p ∈ t := by
  simp only [erase, List.mem_filter] at h
  exact h.1

theorem ne_of_not_isEmpty {s : String} (h : (!s.isEmpty) = true) : s ≠ "" := by
  intro he; subst he; simp at h

theorem realizeOne_inv (tok : Nat) (htok : tok ≤ maxInt) (acc : Snapshot × List Action)
    (a : Action) (ha : actionOK a = true) (h : Inv acc.1) : Inv (realizeOne tok acc a).1 := by
  obtain ⟨hr, ht⟩ := h
  cases a with
  | remember sec n =>
    simp only [actionOK, Bool.and_eq_true] at ha
    refine ⟨fun p hp => ?_, ht⟩
    rcases mem_assign hp with rfl | hp
    · exact ⟨ne_of_not_isEmpty ha.1, ne_of_not_isEmpty ha.2⟩
    · exact hr p hp
  | startTimer id ms _ =>
    refine ⟨hr, fun p hp => ?_⟩
    rcases mem_assign hp with rfl | hp
    · exact ⟨ne_of_not_isEmpty ha, htok⟩
    · exact ht p hp
  | cancelTimer id => exact ⟨hr, fun p hp => ht p (mem_erase hp)⟩
  | send _ => exact ⟨hr, ht⟩
  | call _ => exact ⟨hr, ht⟩

theorem foldl_inv (tok : Nat) (htok : tok ≤ maxInt) (acts : List Action)
    (ha : ∀ a ∈ acts, actionOK a = true) (acc : Snapshot × List Action) (h : Inv acc.1) :
    Inv (acts.foldl (realizeOne tok) acc).1 := by
  induction acts generalizing acc with
  | nil => exact h
  | cons a as ih =>
    simp only [List.foldl_cons]
    exact ih (fun x hx => ha x (by simp [hx])) _
      (realizeOne_inv tok htok acc a (ha a (by simp)) h)

theorem realizeOne_seq (tok : Nat) (acc : Snapshot × List Action) (a : Action) :
    (realizeOne tok acc a).1.seq = acc.1.seq := by cases a <;> rfl

theorem foldl_seq (tok : Nat) (acts : List Action) (acc : Snapshot × List Action) :
    (acts.foldl (realizeOne tok) acc).1.seq = acc.1.seq := by
  induction acts generalizing acc with
  | nil => rfl
  | cons a as ih => simp only [List.foldl_cons]; rw [ih, realizeOne_seq]

theorem realize_seq (s : Snapshot) (T : NodeId) (acts : List Action) :
    (realize s T acts).1.seq = s.seq + 1 := by
  simp only [realize, foldl_seq]

theorem realize_snapOK (s : Snapshot) (T : NodeId) (acts : List Action)
    (hs : SnapOK s) (hT : T ≠ "") (ha : ∀ a ∈ acts, actionOK a = true)
    (hseq : s.seq + 1 ≤ maxInt) : SnapOK (realize s T acts).1 := by
  have hinv := foldl_inv (s.seq + 1) hseq acts ha
    ({ s with state := T, seq := s.seq + 1 }, []) ⟨hs.resume, hs.timers⟩
  exact ⟨by rw [realize_state]; exact hT, hinv.1, hinv.2, by rw [realize_seq]; exact hseq⟩

theorem lookup_mem {α β} [DecidableEq α] {t : List (α × β)} {k : α} {v : β}
    (h : lookup t k = some v) : (k, v) ∈ t := by
  simp only [lookup, Option.map_eq_some_iff] at h
  obtain ⟨p, hp, rfl⟩ := h
  have hk := List.find?_some hp
  simp only [decide_eq_true_eq] at hk
  rw [← hk]
  exact List.mem_of_find?_eq_some hp

theorem table_ok {t : List (NodeId × List Action)} (ht : t.all (fun e => e.2.all actionOK) = true)
    (n : NodeId) : ∀ a ∈ (lookup t n).getD [], actionOK a = true := by
  intro a ha
  cases hl : lookup t n with
  | none => simp [hl] at ha
  | some v =>
    rw [hl] at ha
    have := List.all_eq_true.mp ht _ (lookup_mem hl)
    exact List.all_eq_true.mp this a ha

/-- **Шаг сохраняет допустимость снимка**, если счётчик не переполнен. -/
theorem step_snapOK (m : Machine) (hwf : m.wf = true) (hn : namesOK m = true)
    (s : Snapshot) (ev : Event) (env : HostEnv) (hs : SnapOK s)
    (hseq : (m.step s ev env).1.seq ≤ maxInt) : SnapOK (m.step s ev env).1 := by
  simp only [namesOK, Bool.and_eq_true, List.all_eq_true] at hn
  obtain ⟨⟨⟨⟨hstates, hinit⟩, hrows⟩, hentry⟩, hexit⟩ := hn
  have hvalid := m.step_state_valid hwf s ev env
  have hTne : (m.step s ev env).1.state ≠ "" :=
    ne_of_not_isEmpty (hstates _ hvalid)
  revert hseq hTne hvalid
  unfold Machine.step
  split
  · intro h1 h2 h3
    simp only [Machine.recover] at h1 h2 h3 ⊢
    rw [realize_seq] at h1
    rw [realize_state] at h3
    exact realize_snapOK _ _ _ hs h3
      (by intro a ha
          simp only [List.mem_append, List.mem_map] at ha
          rcases ha with ⟨_, _, rfl⟩ | ha
          · rfl
          · exact hinit a ha) h1
  · split
    · intro _ _ _; exact hs
    · split
      · intro _ _ _; exact hs
      · rename_i r hr
        split
        · intro _ _ _; exact hs
        · rename_i b hb
          intro h1 h2 h3
          simp only [Machine.fire] at h1 h2 h3 ⊢
          rw [realize_seq] at h1
          rw [realize_state] at h3
          refine realize_snapOK _ _ _ hs h3 ?_ h1
          intro a ha
          simp only [List.mem_append] at ha
          rcases ha with (ha | ha) | ha
          · exact table_ok (List.all_eq_true.mpr fun e he =>
              List.all_eq_true.mpr (hexit e he)) _ a ha
          · exact hrows r (List.mem_of_find?_eq_some hr) b (List.mem_of_find?_eq_some hb) a ha
          · exact table_ok (List.all_eq_true.mpr fun e he =>
              List.all_eq_true.mpr (hentry e he)) _ a ha

/-! ## Запрос `step` исполняет доказанный шаг -/

/-- **Протокол исполняет ровно `Machine.step`**: если снимок, событие и
условия разобраны, все нужные условия переданы и счётчик не переполнен,
ответ — результат доказанного шага. -/
theorem handle_step (m : Machine) (kvs : Obj) (j : J) (s : Snapshot) (ev : Event)
    (gs : List (String × Bool))
    (hop : get kvs "op" = some (.str "step")) (hsn : get kvs "snapshot" = some j)
    (hj : decSnapshot j = .ok s) (hev : decEvent kvs = .ok ev) (hgs : decGuards kvs = .ok gs)
    (hmiss : missing m s ev gs = [])
    (hseq : (m.step s ev (envOf gs)).1.seq ≤ maxInt) :
    handleObj m kvs = .ok (result ((get kvs "id").getD .null) (m.step s ev (envOf gs)).1
      (m.step s ev (envOf gs)).2 (decide (s.state ∉ m.states))) := by
  have hle : ¬ (m.step s ev (envOf gs)).1.seq > maxInt := by omega
  simp only [handleObj, hop, hsn, hj, hev, hgs, stepReply, hmiss, List.isEmpty_nil,
    Bool.not_true, bind, Except.bind, pure, Except.pure]
  simp [hle]

/-- **Лишние условия не влияют**: если переданные значения совпадают с любыми
другими на нужных шагу условиях, результат шага тот же (следствие
`step_guard_local`). -/
theorem guards_irrelevant (m : Machine) (s : Snapshot) (ev : Event) (gs : List (String × Bool))
    (env : HostEnv)
    (hagree : ∀ n ∈ m.requiredGuards s.state ev.key, envOf gs n = env n) :
    m.step s ev (envOf gs) = m.step s ev env :=
  m.step_guard_local s ev _ _ hagree

/-- **Замкнутость протокола**: для автомата, прошедшего загрузку, ответ на шаг
из допустимого снимка содержит снимок, который движок читает обратно тем же. -/
theorem reply_readable (m : Machine) (hwf : m.wf = true) (hn : namesOK m = true)
    (s : Snapshot) (ev : Event) (env : HostEnv) (hs : SnapOK s)
    (hseq : (m.step s ev env).1.seq ≤ maxInt) :
    decSnapshot (encSnapshot (m.step s ev env).1) = .ok (m.step s ev env).1 :=
  decSnapshot_encSnapshot _ (step_snapOK m hwf hn s ev env hs hseq)

end Figram.Engine

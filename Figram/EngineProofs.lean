import Figram.Engine
import Figram.CoreProofs
import Figram.FormatProofs

/-!
# Properties of the engine protocol

1. `decSnapshot_encSnapshot` — a snapshot written by the engine is read back as
   **exactly** the same snapshot (if it is within the admissible bounds `SnapOK`).
2. `step_snapOK` — a step of the machine keeps the snapshot admissible if the names
   in the machine are non-empty (`Machine.namesOK`, checked on load) and the counter
   does not overflow (the engine rejects an overflow with the error `seq_overflow`).
   Together with (1): the engine never produces a snapshot it cannot read back.
3. `handle_step` — a `step` request with a full set of conditions executes exactly
   the proved `Machine.step`.
4. `startAt_snapOK` — `start` with `seq` also produces an admissible snapshot.

The checks outside `handleObj` (the strict set of fields, the line size, UTF-8) only
reject requests; an accepted request is handled by `handleObj`.
-/

namespace Figram.Engine
open Figram.Format

/-- An admissible snapshot is exactly what snapshot.schema.json allows. -/
structure SnapOK (s : Snapshot) : Prop where
  state : s.state ≠ ""
  resume : ∀ p ∈ s.resume, p.1 ≠ "" ∧ p.2 ≠ ""
  timers : ∀ p ∈ s.timers, p.1 ≠ "" ∧ p.2 ≤ maxInt
  seq : s.seq ≤ maxInt
  back : ∀ v, s.back = some v → v ≠ ""

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

/-- **A snapshot written by the engine is read back as exactly the same snapshot.** -/
theorem decSnapshot_encSnapshot (s : Snapshot) (h : SnapOK s) :
    decSnapshot (encSnapshot s) = .ok s := by
  obtain ⟨st, res, tms, seq, bk⟩ := s
  obtain ⟨hst, hres, htms, hseq, hbk⟩ := h
  simp only at hst hres htms hseq hbk
  have hkeys : ∀ (A : Obj), (∀ p ∈ A, p.1 ∈ snapshotKeys) → checkKeys A = .ok () := by
    intro A hA
    simp only [checkKeys]
    rw [List.find?_eq_none.mpr (fun p hp => by simp [hA p hp])]
    rfl
  simp only [encSnapshot, decSnapshot]
  rw [hkeys _ (by
    intro p hp
    simp only [List.mem_append, List.mem_singleton] at hp
    rcases hp with (((rfl | hp) | hp) | hp) | hp
    · simp [snapshotKeys]
    all_goals (rw [field_keys _ _ _ p hp]; simp [snapshotKeys]))]
  have h1 := mapM_resume _ hres
  have h2 := mapM_timers _ htms
  cases bk with
  | none =>
    by_cases hr : res = [] <;> by_cases ht : tms = [] <;> by_cases hz : seq = 0 <;>
      simp [checkVersion, Format.get, field, hr, ht, hz, nonEmpty_str _ _ hst,
        h1, h2, boundedNat_num _ _ hseq, bind, Except.bind, pure, Except.pure] <;>
      (try subst hr) <;> (try subst ht) <;> (try subst hz) <;> rfl
  | some b =>
    have hb := nonEmpty_str "`back`" b (hbk b rfl)
    by_cases hr : res = [] <;> by_cases ht : tms = [] <;> by_cases hz : seq = 0 <;>
      simp [checkVersion, Format.get, field, hr, ht, hz, nonEmpty_str _ _ hst, hb,
        h1, h2, boundedNat_num _ _ hseq, bind, Except.bind, pure, Except.pure, Functor.map,
        Except.map] <;>
      (try subst hr) <;> (try subst ht) <;> (try subst hz) <;> rfl

/-! ## A step keeps the snapshot admissible -/

/-- Admissibility of the places, the timers and the place to go back to. -/
def Inv (x : Snapshot) : Prop :=
  (∀ p ∈ x.resume, p.1 ≠ "" ∧ p.2 ≠ "") ∧ (∀ p ∈ x.timers, p.1 ≠ "" ∧ p.2 ≤ maxInt) ∧
    (∀ v, x.back = some v → v ≠ "")

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
  obtain ⟨hr, ht, hb⟩ := h
  cases a with
  | remember sec n =>
    simp only [actionOK, Bool.and_eq_true] at ha
    refine ⟨fun p hp => ?_, ht, hb⟩
    rcases mem_assign hp with rfl | hp
    · exact ⟨ne_of_not_isEmpty ha.1, ne_of_not_isEmpty ha.2⟩
    · exact hr p hp
  | startTimer id ms _ =>
    refine ⟨hr, fun p hp => ?_, hb⟩
    rcases mem_assign hp with rfl | hp
    · exact ⟨ne_of_not_isEmpty ha, htok⟩
    · exact ht p hp
  | cancelTimer id => exact ⟨hr, fun p hp => ht p (mem_erase hp), hb⟩
  | send _ => exact ⟨hr, ht, hb⟩
  | call _ => exact ⟨hr, ht, hb⟩

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

/-- `realize` from a snapshot whose places and timers are admissible (any state). -/
theorem realize_inv_snapOK (s : Snapshot) (T : NodeId) (acts : List Action)
    (hs : Inv s) (hT : T ≠ "") (ha : ∀ a ∈ acts, actionOK a = true)
    (hseq : s.seq + 1 ≤ maxInt) : SnapOK (realize s T acts).1 := by
  have hinv := foldl_inv (s.seq + 1) hseq acts ha ({ s with state := T, seq := s.seq + 1 }, []) hs
  exact ⟨by rw [realize_state]; exact hT, hinv.1, hinv.2.1, by rw [realize_seq]; exact hseq,
    hinv.2.2⟩

theorem realize_snapOK (s : Snapshot) (T : NodeId) (acts : List Action)
    (hs : SnapOK s) (hT : T ≠ "") (ha : ∀ a ∈ acts, actionOK a = true)
    (hseq : s.seq + 1 ≤ maxInt) : SnapOK (realize s T acts).1 := by
  have hinv := foldl_inv (s.seq + 1) hseq acts ha
    ({ s with state := T, seq := s.seq + 1 }, []) ⟨hs.resume, hs.timers, hs.back⟩
  exact ⟨by rw [realize_state]; exact hT, hinv.1, hinv.2.1, by rw [realize_seq]; exact hseq,
    hinv.2.2⟩

/-- Remembering the message left as the place to go back to keeps the snapshot admissible. -/
theorem markBack_snapOK (m : Machine) (s : Snapshot) (T : NodeId) (hs : SnapOK s) :
    SnapOK (m.markBack s T) := by
  unfold Machine.markBack
  split
  · exact hs
  · refine ⟨hs.state, hs.resume, hs.timers, hs.seq, fun v hv => ?_⟩
    simp only [Snapshot.withBack, Option.some.injEq] at hv
    exact hv ▸ hs.state

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

theorem enterBot_seq (m : Machine) (s : Snapshot) (pre : List Action) (ev : Event) (env : HostEnv) :
    (m.enterBot s pre ev env).1.seq = s.seq + 1 := by
  rw [Machine.enterBot_eq_realize]
  split <;> exact realize_seq _ _ _

/-- Entering the bot keeps the places and timers admissible and the state non-empty. -/
theorem enterBot_snapOK (m : Machine) (hwf : m.wf = true) (hn : namesOK m = true) (s : Snapshot)
    (pre : List Action) (ev : Event) (env : HostEnv) (hs : Inv s)
    (hpre : ∀ a ∈ pre, actionOK a = true) (hseq : s.seq + 1 ≤ maxInt) :
    SnapOK (m.enterBot s pre ev env).1 := by
  have hvalid := m.enterBot_state_valid hwf s pre ev env
  simp only [namesOK, Bool.and_eq_true, List.all_eq_true] at hn
  obtain ⟨⟨⟨⟨hstates, hinit⟩, hrows⟩, hentry⟩, _⟩ := hn
  revert hvalid
  rw [Machine.enterBot_eq_realize]
  split
  · rename_i b hb
    intro hvalid
    rw [realize_state] at hvalid
    obtain ⟨r, hr, hbr⟩ := Machine.choose_mem hb
    refine realize_inv_snapOK _ _ _ hs (ne_of_not_isEmpty (hstates _ hvalid)) ?_ hseq
    intro a ha
    simp only [List.mem_append] at ha
    rcases ha with (ha | ha) | ha
    · exact hpre a ha
    · exact hrows r hr b hbr a ha
    · exact table_ok (List.all_eq_true.mpr fun e he => List.all_eq_true.mpr (hentry e he)) _ a ha
  · intro hvalid
    rw [realize_state] at hvalid
    refine realize_inv_snapOK _ _ _ hs (ne_of_not_isEmpty (hstates _ hvalid)) ?_ hseq
    intro a ha
    simp only [List.mem_append] at ha
    rcases ha with ha | ha
    · exact hpre a ha
    · exact hinit a ha

/-- **A step keeps the snapshot admissible** if the counter does not overflow. -/
theorem step_snapOK (m : Machine) (hwf : m.wf = true) (hn : namesOK m = true)
    (s : Snapshot) (ev : Event) (env : HostEnv) (hs : SnapOK s)
    (hseq : (m.step s ev env).1.seq ≤ maxInt) : SnapOK (m.step s ev env).1 := by
  have hn0 := hn
  simp only [namesOK, Bool.and_eq_true, List.all_eq_true] at hn
  obtain ⟨⟨⟨⟨hstates, hinit⟩, hrows⟩, hentry⟩, hexit⟩ := hn
  have hvalid := m.step_state_valid hwf s ev env
  have hTne : (m.step s ev env).1.state ≠ "" :=
    ne_of_not_isEmpty (hstates _ hvalid)
  revert hseq hTne hvalid
  unfold Machine.step
  split
  · intro h1 _ _
    rw [enterBot_seq] at h1
    refine enterBot_snapOK m hwf hn0 s _ ev env ⟨hs.resume, hs.timers, hs.back⟩ ?_ h1
    intro a ha
    simp only [List.mem_map] at ha
    obtain ⟨_, _, rfl⟩ := ha
    rfl
  · split
    · intro _ _ _; exact hs
    · split
      · intro _ _ _; exact hs
      · rename_i r hr
        split
        · intro _ _ _; exact hs
        · rename_i b hb
          intro h1 h2 h3
          simp only [Machine.fire_eq_realize] at h1 h2 h3 ⊢
          rw [realize_seq] at h1
          rw [realize_state] at h3
          refine realize_snapOK _ _ _ (markBack_snapOK m s _ hs) h3 ?_ h1
          intro a ha
          simp only [List.mem_append] at ha
          rcases ha with ((ha | ha) | ha) | ha
          · exact table_ok (List.all_eq_true.mpr fun e he =>
              List.all_eq_true.mpr (hexit e he)) _ a ha
          · -- cancelling the timers left over from a previous document version
            simp only [staleCancels, List.mem_map] at ha
            obtain ⟨_, _, rfl⟩ := ha
            rfl
          · exact hrows r (Machine.row_mem hr) b (List.mem_of_find?_eq_some hb) a ha
          · exact table_ok (List.all_eq_true.mpr fun e he =>
              List.all_eq_true.mpr (hentry e he)) _ a ha

/-! ## A `step` request executes the proved step -/

/-- **The protocol executes exactly `Machine.step`**: if the snapshot, the event and
the conditions are decoded, all needed conditions are given and the counter does not
overflow, the reply is the result of the proved step. -/
theorem handle_step (m : Machine) (kvs : Obj) (j : J) (s : Snapshot) (ev : Event)
    (gs : List (String × Bool))
    (hop : get kvs "op" = some (.str "step")) (hsn : get kvs "snapshot" = some j)
    (hj : decSnapshot j = .ok s) (hev : decEvent kvs = .ok ev) (hgs : decGuards kvs = .ok gs)
    (hmiss : missing m s ev gs = [])
    (hseq : (m.step s ev (envOf gs)).1.seq ≤ maxInt) :
    handleObj m kvs = .ok (result ((get kvs "id").getD .null) (m.step s ev (envOf gs)).1
      (m.step s ev (envOf gs)).2 (decide (s.state ∉ m.states))) := by
  have hle : ¬ (m.step s ev (envOf gs)).1.seq > maxInt := by omega
  simp only [handleObj, hop, hsn, hj, hev, hgs, withCode, Except.mapError, stepReply, hmiss,
    List.isEmpty_nil, Bool.not_true, bind, Except.bind, pure, Except.pure]
  simp [hle]

/-- **Extra conditions do not matter**: if the given values agree with any other
values on the conditions the step needs, the result of the step is the same (a corollary
of `step_guard_local`). -/
theorem guards_irrelevant (m : Machine) (s : Snapshot) (ev : Event) (gs : List (String × Bool))
    (env : HostEnv)
    (hagree : ∀ n ∈ m.requiredGuards s.state ev.key, envOf gs n = env n) :
    m.step s ev (envOf gs) = m.step s ev env :=
  m.step_guard_local s ev _ _ hagree

/-- If `start` with an event asks for no condition, the variant chosen at the initial message
is the same for all condition values that agree with the given ones. -/
theorem missingStart_choose (m : Machine) (e : EventId) (gs : List (String × Bool)) (env : HostEnv)
    (h : missingStart m e gs = []) (hagree : ∀ g v, givenOf gs g = some v → env g = v) :
    m.choose m.initial (.named e) (envOf gs) = m.choose m.initial (.named e) env := by
  have hgiven : ∀ g v, givenOf gs g = some v → envOf gs g = v := by
    intro g v hv
    simp only [givenOf] at hv
    simp [envOf, hv]
  simp only [Machine.choose]
  split
  · rfl
  · rename_i r hr
    have hneed : m.neededGuards m.initial e (givenOf gs) = [] := by
      apply List.eq_nil_iff_forall_not_mem.mpr
      intro g hg
      have hnone : givenOf gs g = none := by
        simp only [Machine.neededGuards, hr] at hg
        exact neededIn_given hg
      have hmem : g ∈ missingStart m e gs := by
        refine List.mem_filter.mpr ⟨(mem_dedup _ _).mpr hg, ?_⟩
        simp only [givenOf, Option.map_eq_none_iff, List.find?_eq_none] at hnone
        simp only [Bool.not_eq_true', List.any_eq_false]
        exact hnone
      rw [h] at hmem
      simp at hmem
    simp only [Machine.neededGuards, hr] at hneed
    rw [find?_neededIn (envOf gs) env hneed hgiven hagree]

/-- **The `need` request is sufficient**: if the engine asks for no condition,
the result of the step is the same for all condition values that agree with the
ones given in the request (conditions not given do not affect the step). -/
theorem missing_nil_step (m : Machine) (s : Snapshot) (ev : Event) (gs : List (String × Bool))
    (env : HostEnv) (h : missing m s ev gs = [])
    (hagree : ∀ n v, givenOf gs n = some v → env n = v) :
    m.step s ev (envOf gs) = m.step s ev env := by
  have hgiven : ∀ n v, givenOf gs n = some v → envOf gs n = v := by
    intro n v hv
    simp only [givenOf] at hv
    simp [envOf, hv]
  unfold Machine.step
  by_cases hn : s.state ∉ m.states
  · simp only [missing, hn, not_false_eq_true, ite_true] at h
    simp only [hn, not_false_eq_true, ite_true]
    cases ev with
    | timer => rfl
    | named e => simp only [Machine.enterBot, missingStart_choose m e gs env h hagree]
  · have hs : s.state ∈ m.states := by simpa using hn
    simp only [missing, hs, not_true_eq_false, ite_false] at h
    simp only [hs, not_true_eq_false, ite_false]
    split
    · rfl
    · rename_i hacc
      have hc : (s.accepts ev && ownsTimer (m.exitOf s.state) ev) = true := by
        simpa using hacc
      simp only [hc, ite_true] at h
      split
      · rfl
      · rename_i r hr
        have hneed : m.neededGuards s.state ev.key (givenOf gs) = [] := by
          apply List.eq_nil_iff_forall_not_mem.mpr
          intro n hn
          have hnone : givenOf gs n = none := by
            simp only [Machine.neededGuards, hr] at hn
            exact neededIn_given hn
          have hmem : n ∈ (dedup (m.neededGuards s.state ev.key (givenOf gs))).filter
              (fun n => !(gs.any (·.1 == n))) := by
            refine List.mem_filter.mpr ⟨(mem_dedup _ _).mpr hn, ?_⟩
            simp only [givenOf, Option.map_eq_none_iff, List.find?_eq_none] at hnone
            simp only [Bool.not_eq_true', List.any_eq_false]
            exact hnone
          rw [h] at hmem
          simp at hmem
        simp only [Machine.neededGuards, hr] at hneed
        rw [find?_neededIn (envOf gs) env hneed hgiven hagree]

/-- **Closure of the protocol**: for a machine that passed loading, the reply to a step
from an admissible snapshot contains a snapshot that the engine reads back unchanged. -/
theorem reply_readable (m : Machine) (hwf : m.wf = true) (hn : namesOK m = true)
    (s : Snapshot) (ev : Event) (env : HostEnv) (hs : SnapOK s)
    (hseq : (m.step s ev env).1.seq ≤ maxInt) :
    decSnapshot (encSnapshot (m.step s ev env).1) = .ok (m.step s ev env).1 :=
  decSnapshot_encSnapshot _ (step_snapOK m hwf hn s ev env hs hseq)

/-! ## `start` with a continued counter -/

/-- `start` without `seq` is the special case of `startAt` with `seq` 0. -/
theorem startAt_zero (m : Machine) : startAt m 0 = m.start := rfl

/-- **`start` with `seq` produces an admissible snapshot** if the next `seq` does not
overflow (otherwise the engine replies `seq_overflow`). -/
theorem startAt_snapOK (m : Machine) (hwf : m.wf = true) (hn : namesOK m = true) (n : Nat)
    (hseq : n + 1 ≤ maxInt) : SnapOK (startAt m n).1 := by
  simp only [namesOK, Bool.and_eq_true, List.all_eq_true] at hn
  obtain ⟨⟨⟨⟨hstates, hinit⟩, _⟩, _⟩, _⟩ := hn
  have hi := (Machine.wf_parts hwf).1
  have hinv := foldl_inv (n + 1) hseq m.initActions hinit
    ({ Snapshot.fresh with state := m.initial, seq := n + 1 }, [])
    ⟨fun p hp => by simp [Snapshot.fresh] at hp, fun p hp => by simp [Snapshot.fresh] at hp,
      fun v hv => by simp [Snapshot.fresh] at hv⟩
  refine ⟨?_, hinv.1, hinv.2.1, ?_, hinv.2.2⟩
  · show (realize _ m.initial m.initActions).1.state ≠ ""
    rw [realize_state]
    exact ne_of_not_isEmpty (hstates _ hi)
  · show (realize _ m.initial m.initActions).1.seq ≤ maxInt
    rw [realize_seq]
    exact hseq

/-! ## `start` with a first event -/

/-- Without a matching variant, `start` with an event is `start` with `seq`. -/
theorem startWith_unmatched_startAt (m : Machine) (n : Nat) (ev : Event) (env : HostEnv)
    (hb : m.choose m.initial ev env = none) : m.startWith n ev env = startAt m n :=
  m.startWith_unmatched n ev env hb

/-- **`start` with an event produces an admissible snapshot** if the next `seq` does not
overflow (otherwise the engine replies `seq_overflow`). -/
theorem startWith_snapOK (m : Machine) (hwf : m.wf = true) (hn : namesOK m = true) (n : Nat)
    (ev : Event) (env : HostEnv) (hseq : n + 1 ≤ maxInt) : SnapOK (m.startWith n ev env).1 :=
  enterBot_snapOK m hwf hn ⟨"", [], [], n, none⟩ [] ev env
    ⟨fun p hp => by simp at hp, fun p hp => by simp at hp, fun v hv => by simp at hv⟩
    (fun a ha => by simp at ha) hseq

/-- **The protocol executes exactly `Machine.startWith`**: if the event and the conditions are
decoded, all needed conditions are given and the counter does not overflow, the reply to
`start` with an event is the result of the proved entry. -/
theorem handle_start (m : Machine) (kvs : Obj) (n : Nat) (e : EventId) (gs : List (String × Bool))
    (hop : get kvs "op" = some (.str "start")) (hn : decStartSeq kvs = .ok n)
    (hj : (get kvs "event").isSome = true) (hev : decStartEvent kvs = .ok e)
    (hgs : decGuards kvs = .ok gs) (hmiss : missingStart m e gs = [])
    (hseq : (m.startWith n (.named e) (envOf gs)).1.seq ≤ maxInt) :
    handleObj m kvs = .ok (result ((get kvs "id").getD .null) (m.startWith n (.named e) (envOf gs)).1
      (m.startWith n (.named e) (envOf gs)).2 false) := by
  have hle : ¬ (m.startWith n (.named e) (envOf gs)).1.seq > maxInt := by omega
  obtain ⟨j, hj⟩ := Option.isSome_iff_exists.mp hj
  simp only [handleObj, hop, hn, hj, withCode, Except.mapError, hev, hgs, startReply, hmiss,
    List.isEmpty_nil, Bool.not_true, bind, Except.bind, pure, Except.pure]
  simp [hle]

/-- **The `need` of `start` is sufficient**: if the engine asks for no condition, `start` with
the event gives the same result for all condition values that agree with the given ones. -/
theorem missingStart_nil (m : Machine) (n : Nat) (e : EventId) (gs : List (String × Bool))
    (env : HostEnv) (h : missingStart m e gs = [])
    (hagree : ∀ g v, givenOf gs g = some v → env g = v) :
    m.startWith n (.named e) (envOf gs) = m.startWith n (.named e) env := by
  simp only [Machine.startWith, Machine.enterBot, missingStart_choose m e gs env h hagree]

end Figram.Engine

import Figram.Core

/-!
# Properties of the core

1. `step_state_valid` — after any step from **any** snapshot the user is on an
   existing message (if the machine passed the `wf` check).
   A snapshot with a deleted message is recovered (`recover`).
2. `start_state_valid` — the same for the first entry.
3. `step_guard_local` — a step depends only on the conditions in `requiredGuards`.
4. Entering the bot with an event (`startWith`): `enterBot_state_valid`,
   `enterBot_guard_local`, `startWith_eq_step` (a step from the initial message without its
   entry), `startWith_initial` (a variant into the initial message enters it once) and
   `startWith_unmatched` (no variant: the plain first entry); `step_recover` (a step from a
   snapshot whose message no longer exists enters the bot again with the event) and
   `step_recover_timer` (a timer event there is the plain recovery).
5. "Back to the previous section": `fire_back` (a move across a section boundary remembers the
   message left, a move inside a section keeps the place), `fire_back_swap` ("back" leads to the
   place, and from there "back" leads back again), `step_back` (the place is always one the user
   left) and `enterBot_back` (entering the bot keeps it).
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

theorem realizeOne_back (tok : Nat) (acc : Snapshot × List Action) (a : Action) :
    (realizeOne tok acc a).1.back = acc.1.back := by
  cases a <;> rfl

theorem foldl_realize_back (tok : Nat) (acts : List Action) (acc : Snapshot × List Action) :
    (acts.foldl (realizeOne tok) acc).1.back = acc.1.back := by
  induction acts generalizing acc with
  | nil => rfl
  | cons a as ih => simp only [List.foldl_cons]; rw [ih, realizeOne_back]

/-- Executing actions keeps the place to go back to. -/
theorem realize_back (s : Snapshot) (T : NodeId) (acts : List Action) :
    (realize s T acts).1.back = s.back := by
  simp only [realize, foldl_realize_back]

theorem realizeOne_withBack (tok : Nat) (x : Snapshot) (out : List Action) (bk : Option NodeId)
    (a : Action) : realizeOne tok (x.withBack bk, out) a =
      ((realizeOne tok (x, out) a).1.withBack bk, (realizeOne tok (x, out) a).2) := by
  cases a <;> rfl

theorem foldl_withBack (tok : Nat) (acts : List Action) (x : Snapshot) (out : List Action)
    (bk : Option NodeId) : acts.foldl (realizeOne tok) (x.withBack bk, out) =
      ((acts.foldl (realizeOne tok) (x, out)).1.withBack bk, (acts.foldl (realizeOne tok) (x, out)).2) := by
  induction acts generalizing x out with
  | nil => rfl
  | cons a as ih => simp only [List.foldl_cons]; rw [realizeOne_withBack, ih]

/-- The place to go back to does not take part in executing actions. -/
theorem realize_withBack (s : Snapshot) (T : NodeId) (acts : List Action) (bk : Option NodeId) :
    realize (s.withBack bk) T acts = ((realize s T acts).1.withBack bk, (realize s T acts).2) :=
  foldl_withBack _ acts { s with state := T, seq := s.seq + 1 } [] bk

theorem lookup_some_mem {α β} [DecidableEq α] {t : List (α × β)} {k : α} {v : β}
    (h : lookup t k = some v) : (k, v) ∈ t := by
  simp only [lookup, Option.map_eq_some_iff] at h
  obtain ⟨p, hp, rfl⟩ := h
  have hk := List.find?_some hp
  simp only [decide_eq_true_eq] at hk
  rw [← hk]
  exact List.mem_of_find?_eq_some hp

/-- The choice of a variant does not depend on the variants after the first unconditional one. -/
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

/-- If no conditions are missing, the choice of a variant is the same for all values
that agree with the known ones. -/
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

/-- `realize` appends the actions for the host to what it was given. -/
theorem foldl_realize_out (tok : Nat) (acts : List Action) (x : Snapshot) (out : List Action) :
    acts.foldl (realizeOne tok) (x, out) =
      ((acts.foldl (realizeOne tok) (x, [])).1, out ++ (acts.foldl (realizeOne tok) (x, [])).2) := by
  induction acts generalizing x out with
  | nil => simp
  | cons a as ih =>
    simp only [List.foldl_cons]
    have h1 : realizeOne tok (x, out) a =
        ((realizeOne tok (x, []) a).1, out ++ (realizeOne tok (x, []) a).2) := by
      cases a <;> simp [realizeOne]
    rw [h1, ih, ih (realizeOne tok (x, []) a).1 (realizeOne tok (x, []) a).2]
    simp

/-! ## Clearing the timers at once

`realize` is the specification of executing actions; the machine clears the snapshot's timers
at once where all of them are cancelled (`realizeClear`). These lemmas show that it computes
exactly `realize`, so every theorem about the machine is a theorem about `realize`. -/

/-- Actions that only cancel timers remove the cancelled names and go to the reply. -/
theorem foldl_cancels_only (tok : Nat) (pre : List Action) (x : Snapshot) (out : List Action)
    (h : ∀ a ∈ pre, a.isCancel = true) :
    pre.foldl (realizeOne tok) (x, out) =
      ({ x with timers := x.timers.filter (fun p => !pre.contains (.cancelTimer p.1)) }, out ++ pre) := by
  induction pre generalizing x out with
  | nil =>
    obtain ⟨st, res, tms, seq, bk⟩ := x
    simp only [List.foldl_nil, List.contains_nil, Bool.not_false, List.append_nil,
      List.filter_eq_self.mpr (fun _ _ => rfl)]
  | cons a as ih =>
    cases a with
    | cancelTimer id =>
      simp only [List.foldl_cons, realizeOne]
      rw [ih _ _ (fun b hb => h b (List.mem_cons_of_mem _ hb))]
      simp only [erase, List.filter_filter, List.append_assoc, List.singleton_append]
      congr 2
      apply List.filter_congr
      intro p _
      by_cases hp : p.1 = id <;> simp [hp]
    | _ => exact absurd (h _ List.mem_cons_self) (by simp [Action.isCancel])

/-- **Clearing the timers at once is `realize`**: if `pre` only cancels timers and cancels every
timer of the snapshot, `realize` of `pre` and then `acts` is `realizeClear`. -/
theorem realize_clear (s : Snapshot) (T : NodeId) (pre acts : List Action)
    (hpre : ∀ a ∈ pre, a.isCancel = true) (hcov : ∀ p ∈ s.timers, Action.cancelTimer p.1 ∈ pre) :
    realize s T (pre ++ acts) = realizeClear s T pre acts := by
  have hnil : s.timers.filter (fun p => !pre.contains (.cancelTimer p.1)) = [] := by
    apply List.filter_eq_nil_iff.mpr
    intro p hp
    simpa using hcov p hp
  simp only [realize, realizeClear, List.foldl_append]
  rw [foldl_cancels_only _ _ _ _ hpre, foldl_realize_out]
  simp only [hnil, List.nil_append]

theorem realizeAfter_eq (s : Snapshot) (T : NodeId) (pre acts : List Action) :
    realizeAfter s T pre acts = realize s T (pre ++ acts) := by
  unfold realizeAfter
  split
  · rename_i h
    subst h
    refine (realize_clear s T _ acts ?_ (fun p hp => List.mem_map.mpr ⟨p, hp, rfl⟩)).symm
    intro a ha
    obtain ⟨_, _, rfl⟩ := List.mem_map.mp ha
    rfl
  · rfl

theorem staleCancels_cancel (exit : List Action) (s : Snapshot) :
    ∀ a ∈ staleCancels exit s, a.isCancel = true := by
  intro a ha
  simp only [staleCancels, List.mem_map] at ha
  obtain ⟨_, _, rfl⟩ := ha
  rfl

theorem markBack_timers (m : Machine) (s : Snapshot) (T : NodeId) :
    (m.markBack s T).timers = s.timers := by
  unfold markBack; split <;> rfl

/-- **A move is `realize`** of the exit actions, the cancels of the other timers, the variant's
actions and the entry actions. -/
theorem fire_eq_realize (m : Machine) (s : Snapshot) (b : FBranch) :
    m.fire s b = realize (m.markBack s (m.resolve s b.target)) (m.resolve s b.target)
      (m.exitOf s.state ++ staleCancels (m.exitOf s.state) s ++ b.actions ++
        m.entryOf (m.resolve s b.target)) := by
  simp only [fire]
  split
  · rename_i hx
    rw [List.append_assoc (m.exitOf s.state ++ staleCancels (m.exitOf s.state) s)]
    refine (realize_clear _ _ _ _ ?_ ?_).symm
    · intro a ha
      rcases List.mem_append.mp ha with ha | ha
      · exact List.all_eq_true.mp hx a ha
      · exact staleCancels_cancel _ _ a ha
    · intro p hp
      rw [markBack_timers] at hp
      by_cases hc : (m.exitOf s.state).contains (.cancelTimer p.1) = true
      · exact List.mem_append_left _ (by simpa using hc)
      · refine List.mem_append_right _ (List.mem_map.mpr ⟨p, ?_, rfl⟩)
        exact List.mem_filter.mpr ⟨hp, by simpa using hc⟩
  · rfl

/-- `restart` is `realize` of cancelling all the snapshot's timers and the first-entry actions. -/
theorem recover_eq_realize (m : Machine) (s : Snapshot) :
    m.recover s = realize s m.initial (s.timers.map (fun p => .cancelTimer p.1) ++ m.initActions) := by
  unfold recover
  rw [← realizeAfter_eq]
  simp [realizeAfter]

/-- Entering the bot is `realize` of `pre`, the chosen variant's actions and the entry of its
target, or of `pre` and the first-entry actions. -/
theorem enterBot_eq_realize (m : Machine) (s : Snapshot) (pre : List Action) (ev : Event)
    (env : HostEnv) :
    m.enterBot s pre ev env =
      match m.choose m.initial ev env with
      | some b => realize s (m.resolve s b.target) (pre ++ b.actions ++ m.entryOf (m.resolve s b.target))
      | none => realize s m.initial (pre ++ m.initActions) := by
  unfold enterBot
  cases m.choose m.initial ev env <;> simp only [realizeAfter_eq, List.append_assoc]

/-- Entering the bot with a chosen variant is `realize` of `pre`, its actions and the entry of
its target. -/
theorem enterBot_eq_realize_some (m : Machine) (s : Snapshot) (pre : List Action) (ev : Event)
    (env : HostEnv) (b : FBranch) (hb : m.choose m.initial ev env = some b) :
    m.enterBot s pre ev env =
      realize s (m.resolve s b.target) (pre ++ b.actions ++ m.entryOf (m.resolve s b.target)) := by
  rw [enterBot_eq_realize, hb]

/-- Entering the bot without a chosen variant is `realize` of `pre` and the first-entry actions. -/
theorem enterBot_eq_realize_none (m : Machine) (s : Snapshot) (pre : List Action) (ev : Event)
    (env : HostEnv) (hb : m.choose m.initial ev env = none) :
    m.enterBot s pre ev env = realize s m.initial (pre ++ m.initActions) := by
  rw [enterBot_eq_realize, hb]

/-- The arrow found is one of the machine's arrows. -/
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
  induction t with
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
  | back fb ih =>
    have hfb : m.targetOK fb = true := by simpa [targetOK] using ht
    simp only [resolve]
    split
    · split
      · assumption
      · exact ih hfb
    · exact ih hfb

theorem find?_congr {α} (l : List α) (p q : α → Bool)
    (h : ∀ x ∈ l, p x = q x) : l.find? p = l.find? q := by
  induction l with
  | nil => rfl
  | cons x xs ih =>
    have hx := h x (by simp)
    have hxs : ∀ y ∈ xs, p y = q y := fun y hy => h y (by simp [hy])
    simp only [List.find?_cons, hx, ih hxs]

/-! ## Entering the bot with an event -/

theorem choose_mem {m : Machine} {n : NodeId} {ev : Event} {env : HostEnv} {b : FBranch}
    (h : m.choose n ev env = some b) : ∃ r ∈ m.rows, b ∈ r.branches := by
  cases ev with
  | timer => simp [choose] at h
  | named e =>
    simp only [choose] at h
    split at h
    · cases h
    · rename_i r hr
      exact ⟨r, row_mem hr, List.mem_of_find?_eq_some h⟩

/-- The choice depends only on the conditions in `requiredGuards`, as in `step_guard_local`. -/
theorem choose_guard_local (m : Machine) (n : NodeId) (ev : Event) (env₁ env₂ : HostEnv)
    (h : ∀ g ∈ m.rowGuards n ev.key, env₁ g = env₂ g) :
    m.choose n ev env₁ = m.choose n ev env₂ := by
  cases ev with
  | timer => rfl
  | named e =>
    simp only [choose]
    unfold rowGuards at h
    split
    · rfl
    · rename_i r hr
      simp only [Event.key, hr] at h
      have hfind : (untilUnguarded (·.guard) r.branches).find? (fun b => guardOK env₁ b.guard)
          = (untilUnguarded (·.guard) r.branches).find? (fun b => guardOK env₂ b.guard) := by
        apply find?_congr
        intro b hb
        cases hg : b.guard with
        | none => rfl
        | some g =>
          simp only [guardOK]
          exact h g (List.mem_filterMap.mpr ⟨b, hb, hg⟩)
      rw [find?_untilUnguarded env₁, find?_untilUnguarded env₂, hfind]

/-- **Entering the bot depends only on the conditions of the initial message's arrow.** -/
theorem enterBot_guard_local (m : Machine) (s : Snapshot) (pre : List Action) (ev : Event)
    (env₁ env₂ : HostEnv) (h : ∀ g ∈ m.requiredGuards m.initial ev.key, env₁ g = env₂ g) :
    m.enterBot s pre ev env₁ = m.enterBot s pre ev env₂ := by
  have h' : ∀ g ∈ m.rowGuards m.initial ev.key, env₁ g = env₂ g := by
    simpa [requiredGuards] using h
  simp only [enterBot, m.choose_guard_local m.initial ev env₁ env₂ h']

/-- **After entering the bot with an event the user is on an existing message.** -/
theorem enterBot_state_valid (m : Machine) (hwf : m.wf = true) (s : Snapshot)
    (pre : List Action) (ev : Event) (env : HostEnv) :
    (m.enterBot s pre ev env).1.state ∈ m.states := by
  obtain ⟨hinit, hrows, _⟩ := wf_parts hwf
  rw [enterBot_eq_realize]
  split
  · rename_i b hb
    obtain ⟨r, hr, hbr⟩ := choose_mem hb
    simp only [realize_state]
    exact resolve_mem hwf s _ (hrows r hr b hbr)
  · simp only [realize_state]; exact hinit

theorem start_state_valid (m : Machine) (hwf : m.wf = true) : m.start.1.state ∈ m.states := by
  simp only [start, realize_state]
  exact (wf_parts hwf).1

theorem step_state_valid (m : Machine) (hwf : m.wf = true)
    (s : Snapshot) (ev : Event) (env : HostEnv) :
    (m.step s ev env).1.state ∈ m.states := by
  obtain ⟨hinit, hrows, _⟩ := wf_parts hwf
  unfold step
  split
  · exact enterBot_state_valid m hwf s _ ev env
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
          simp only [fire_eq_realize, realize_state]
          exact resolve_mem hwf s _ (hrows r (row_mem hr) b
            (List.mem_of_find?_eq_some hb))

/-- A firing of a timer the current message does not own (its exit does not cancel
the timer) reads no arrows: the snapshot stays the same and there are no actions.
So an arrow `timer:X` of a message without timer X is unreachable. -/
theorem step_unowned_timer (m : Machine) (s : Snapshot) (id : String) (tok : Nat) (env : HostEnv)
    (hs : s.state ∈ m.states) (h : (m.exitOf s.state).contains (.cancelTimer id) = false) :
    m.step s (.timer id tok) env = (s, []) := by
  unfold step
  simp only [hs, not_true_eq_false, ite_false, ownsTimer, h, Bool.and_false, Bool.not_false,
    ite_true]

theorem step_guard_local (m : Machine) (s : Snapshot) (ev : Event) (env₁ env₂ : HostEnv)
    (h : ∀ n ∈ m.requiredGuards s.state ev.key, env₁ n = env₂ n) :
    m.step s ev env₁ = m.step s ev env₂ := by
  by_cases hn : s.state ∉ m.states
  · have h' : ∀ g ∈ m.requiredGuards m.initial ev.key, env₁ g = env₂ g := by
      simpa [requiredGuards, hn] using h
    simp only [step, hn]
    exact enterBot_guard_local m s _ ev env₁ env₂ h'
  have hs : s.state ∈ m.states := by simpa using hn
  unfold step
  simp only [requiredGuards, hs, ite_true, rowGuards] at h
  split
  · contradiction
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

/-- Cancelling timers of a snapshot that has none changes nothing in it. -/
theorem foldl_cancels (tok : Nat) (acts : List Action) (x : Snapshot) (out : List Action)
    (hx : x.timers = []) (h : ∀ a ∈ acts, ∃ id, a = .cancelTimer id) :
    acts.foldl (realizeOne tok) (x, out) = (x, out ++ acts) := by
  induction acts generalizing out with
  | nil => simp
  | cons a as ih =>
    obtain ⟨id, rfl⟩ := h a (by simp)
    have hx' : { x with timers := erase x.timers id } = x := by
      obtain ⟨st, res, tms, seq, bk⟩ := x
      simp only at hx
      subst hx
      rfl
    simp only [List.foldl_cons, realizeOne, hx']
    rw [ih _ (fun b hb => h b (by simp [hb]))]
    simp

/-- The target depends only on the remembered places and the place to go back to. -/
theorem resolve_congr (m : Machine) {s s' : Snapshot} (hr : s.resume = s'.resume)
    (hb : s.back = s'.back) (t : Target) : m.resolve s t = m.resolve s' t := by
  induction t with
  | node _ => rfl
  | resume sec => simp only [resolve, hr]
  | back fb ih => simp only [resolve, hb, ih]

theorem withBack_withBack (s : Snapshot) (a b : Option NodeId) :
    (s.withBack a).withBack b = s.withBack b := rfl

theorem withBack_eq_self {s : Snapshot} {b : Option NodeId} (h : s.back = b) : s.withBack b = s := by
  subst h; rfl

/-- A move from a snapshot without a place to go back to, with that place cleared again, is
`realize` without remembering it. -/
theorem fire_withBack_none (m : Machine) (s : Snapshot) (b : FBranch) (hs : s.back = none) :
    ((m.fire s b).1.withBack none, (m.fire s b).2) =
      realize s (m.resolve s b.target)
        (m.exitOf s.state ++ staleCancels (m.exitOf s.state) s ++ b.actions ++
          m.entryOf (m.resolve s b.target)) := by
  have hb : ∀ acts T, (realize s T acts).1.withBack none = (realize s T acts).1 := fun acts T =>
    withBack_eq_self (by rw [realize_back, hs])
  rw [fire_eq_realize]
  simp only [markBack]
  split
  · rw [hb]
  · rw [realize_withBack, withBack_withBack, hb]

/-- **Entering the bot with an event is a step from the initial message without entering it.**
From the snapshot `v` that stands on the initial message with no timers, no remembered
places and no place to go back to, a step on an event that chooses a variant gives the
snapshot of `startWith`, except that the step may remember the initial message as the place
to go back to (the step leaves it; `startWith` does not), and its actions are the exit actions
of the initial message (they only cancel its timers, which were never started) followed by the
actions of `startWith`. -/
theorem startWith_eq_step (m : Machine) (n : Nat) (e : EventId) (env : HostEnv) (b : FBranch)
    (hi : m.initial ∈ m.states) (hb : m.choose m.initial (.named e) env = some b)
    (hexit : ∀ a ∈ m.exitOf m.initial, ∃ id, a = .cancelTimer id) :
    ((m.step ⟨m.initial, [], [], n, none⟩ (.named e) env).1.withBack none,
      (m.step ⟨m.initial, [], [], n, none⟩ (.named e) env).2) =
      ((m.startWith n (.named e) env).1, m.exitOf m.initial ++ (m.startWith n (.named e) env).2) := by
  have hrow : ∃ r, m.row m.initial e = some r ∧ r.branches.find? (fun b => guardOK env b.guard) = some b := by
    simp only [choose] at hb
    split at hb
    · cases hb
    · rename_i r hr; exact ⟨r, hr, hb⟩
  obtain ⟨r, hr, hfb⟩ := hrow
  have hstale : staleCancels (m.exitOf m.initial) ⟨m.initial, [], [], n, none⟩ = [] := rfl
  have hstep : m.step ⟨m.initial, [], [], n, none⟩ (.named e) env = m.fire ⟨m.initial, [], [], n, none⟩ b := by
    simp only [step, hi, not_true_eq_false, ite_false, Snapshot.accepts, ownsTimer, Bool.and_self,
      Bool.not_true, Bool.false_eq_true, Event.key, hr, hfb]
  rw [hstep, fire_withBack_none m _ b rfl]
  simp only [startWith, enterBot, hb, hstale, realizeAfter_eq, List.append_nil, List.nil_append]
  show List.foldl _ _ ((m.exitOf m.initial ++ b.actions) ++ _) = _
  rw [List.append_assoc, List.foldl_append,
    foldl_cancels _ _ _ _ rfl hexit, foldl_realize_out,
    resolve_congr m (s := ⟨m.initial, [], [], n, none⟩) (s' := ⟨"", [], [], n, none⟩) rfl rfl]
  rfl

/-- **A variant into the initial message enters it once**: if the chosen variant leads to the
initial message, entering with the event is the first entry with the variant's actions in
front (one entry, one `send` of the initial message). -/
theorem startWith_initial (m : Machine) (n : Nat) (ev : Event) (env : HostEnv) (b : FBranch)
    (hb : m.choose m.initial ev env = some b)
    (hT : m.resolve ⟨"", [], [], n, none⟩ b.target = m.initial)
    (hinit : m.entryOf m.initial = m.initActions) :
    m.startWith n ev env =
      realize ⟨"", [], [], n, none⟩ m.initial (b.actions ++ m.initActions) := by
  simp only [startWith, enterBot, hb, hT, hinit, realizeAfter_eq, List.nil_append]

/-- **A snapshot whose message no longer exists enters the bot again with the event**: the
timers of the old place are cancelled first and the remembered places are kept. -/
theorem step_recover (m : Machine) (s : Snapshot) (ev : Event) (env : HostEnv)
    (hs : s.state ∉ m.states) :
    m.step s ev env = m.enterBot s (s.timers.map (fun p => .cancelTimer p.1)) ev env := by
  simp only [step, hs, not_false_eq_true, ite_true]

/-- A timer event does not move a recovered user: it is the plain recovery that `restart` also
uses (with the remembered places kept). -/
theorem step_recover_timer (m : Machine) (s : Snapshot) (id : String) (tok : Nat) (env : HostEnv)
    (hs : s.state ∉ m.states) : m.step s (.timer id tok) env = m.recover s := by
  simp only [step, hs, not_false_eq_true, ite_true, enterBot_eq_realize, choose, recover_eq_realize]

/-- Without a matching variant, entering with an event is the plain first entry. -/
theorem startWith_unmatched (m : Machine) (n : Nat) (ev : Event) (env : HostEnv)
    (hb : m.choose m.initial ev env = none) :
    m.startWith n ev env = realize ⟨"", [], [], n, none⟩ m.initial m.initActions := by
  simp only [startWith, enterBot, hb, realizeAfter_eq, List.nil_append]

/-! ## Back to the previous section -/

/-- **The place to go back to after a move**: the message left if the move crosses a section
boundary, otherwise the place stays. -/
theorem fire_back (m : Machine) (s : Snapshot) (b : FBranch) :
    (m.fire s b).1.back =
      if m.zoneOf s.state = m.zoneOf (m.resolve s b.target) then s.back else some s.state := by
  rw [fire_eq_realize, realize_back]
  simp only [markBack]
  split <;> rfl

/-- **"Back" and back again**: if the place to go back to is a message of another section,
"back" leads to it and remembers the message left, so the next "back" returns. -/
theorem fire_back_swap (m : Machine) (s : Snapshot) (b : FBranch) (fb : Target) (v : NodeId)
    (ht : b.target = .back fb) (hb : s.back = some v) (hv : v ∈ m.states)
    (hz : m.zoneOf s.state ≠ m.zoneOf v) :
    (m.fire s b).1.state = v ∧ (m.fire s b).1.back = some s.state := by
  have hT : m.resolve s b.target = v := by simp [ht, resolve, hb, hv]
  refine ⟨by simp only [fire_eq_realize, realize_state, hT], ?_⟩
  rw [fire_back, hT]
  simp [hz]

/-- **Entering the bot keeps the place to go back to** (the first entry with an event and the
recovery of a snapshot whose message no longer exists leave no message of the machine). -/
theorem enterBot_back (m : Machine) (s : Snapshot) (pre : List Action) (ev : Event)
    (env : HostEnv) : (m.enterBot s pre ev env).1.back = s.back := by
  rw [enterBot_eq_realize]
  split <;> exact realize_back _ _ _

/-- **The place to go back to is always a message the user left**: after a step it is the old
place or the message of the snapshot, which is a message of the machine. -/
theorem step_back (m : Machine) (s : Snapshot) (ev : Event) (env : HostEnv) :
    (m.step s ev env).1.back = s.back ∨
      (s.state ∈ m.states ∧ (m.step s ev env).1.back = some s.state) := by
  unfold step
  split
  · exact Or.inl (enterBot_back m s _ ev env)
  · rename_i hs
    have hs : s.state ∈ m.states := by simpa using hs
    split
    · exact Or.inl rfl
    · split
      · exact Or.inl rfl
      · split
        · exact Or.inl rfl
        · rename_i b _
          rw [fire_back]
          split
          · exact Or.inl rfl
          · exact Or.inr ⟨hs, rfl⟩

end Machine
end Figram

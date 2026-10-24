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
  unfold enterBot
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
          simp only [fire, realize_state]
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

/-- Cancelling timers of a snapshot that has none changes nothing in it. -/
theorem foldl_cancels (tok : Nat) (acts : List Action) (x : Snapshot) (out : List Action)
    (hx : x.timers = []) (h : ∀ a ∈ acts, ∃ id, a = .cancelTimer id) :
    acts.foldl (realizeOne tok) (x, out) = (x, out ++ acts) := by
  induction acts generalizing out with
  | nil => simp
  | cons a as ih =>
    obtain ⟨id, rfl⟩ := h a (by simp)
    have hx' : { x with timers := erase x.timers id } = x := by
      obtain ⟨st, res, tms, seq⟩ := x
      simp only at hx
      subst hx
      rfl
    simp only [List.foldl_cons, realizeOne, hx']
    rw [ih _ (fun b hb => h b (by simp [hb]))]
    simp

/-- **Entering the bot with an event is a step from the initial message without entering it.**
From the snapshot `v` that stands on the initial message with no timers and no remembered
places, a step on an event that chooses a variant gives the snapshot of `startWith`, and its
actions are the exit actions of the initial message (they only cancel its timers, which were
never started) followed by the actions of `startWith`. -/
theorem startWith_eq_step (m : Machine) (n : Nat) (e : EventId) (env : HostEnv) (b : FBranch)
    (hi : m.initial ∈ m.states) (hb : m.choose m.initial (.named e) env = some b)
    (hexit : ∀ a ∈ m.exitOf m.initial, ∃ id, a = .cancelTimer id) :
    m.step ⟨m.initial, [], [], n⟩ (.named e) env =
      ((m.startWith n (.named e) env).1, m.exitOf m.initial ++ (m.startWith n (.named e) env).2) := by
  have hrow : ∃ r, m.row m.initial e = some r ∧ r.branches.find? (fun b => guardOK env b.guard) = some b := by
    simp only [choose] at hb
    split at hb
    · cases hb
    · rename_i r hr; exact ⟨r, hr, hb⟩
  obtain ⟨r, hr, hfb⟩ := hrow
  have hstale : staleCancels (m.exitOf m.initial) ⟨m.initial, [], [], n⟩ = [] := rfl
  simp only [step, startWith, enterBot, hb, fire, hi, not_true_eq_false, ite_false, Snapshot.accepts,
    ownsTimer, Bool.and_self, Bool.not_true, Bool.false_eq_true, Event.key, hr, hfb, hstale,
    List.append_nil, List.nil_append]
  show List.foldl _ _ ((m.exitOf m.initial ++ b.actions) ++ _) = _
  rw [List.append_assoc, List.foldl_append,
    foldl_cancels _ _ _ _ rfl hexit, foldl_realize_out]
  rfl

/-- **A variant into the initial message enters it once**: if the chosen variant leads to the
initial message, entering with the event is the first entry with the variant's actions in
front (one entry, one `send` of the initial message). -/
theorem startWith_initial (m : Machine) (n : Nat) (ev : Event) (env : HostEnv) (b : FBranch)
    (hb : m.choose m.initial ev env = some b)
    (hT : m.resolve ⟨"", [], [], n⟩ b.target = m.initial)
    (hinit : m.entryOf m.initial = m.initActions) :
    m.startWith n ev env =
      realize ⟨"", [], [], n⟩ m.initial (b.actions ++ m.initActions) := by
  simp only [startWith, enterBot, hb, hT, hinit, List.nil_append]

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
  simp only [step, hs, not_false_eq_true, ite_true, enterBot, choose, recover]

/-- Without a matching variant, entering with an event is the plain first entry. -/
theorem startWith_unmatched (m : Machine) (n : Nat) (ev : Event) (env : HostEnv)
    (hb : m.choose m.initial ev env = none) :
    m.startWith n ev env = realize ⟨"", [], [], n⟩ m.initial m.initActions := by
  simp only [startWith, enterBot, hb, List.nil_append]

end Machine
end Figram

import Figram.EngineProofs

/-!
# Properties of actions with outcomes, background actions and variants without a target

1. **Waits** (`Snapshot.pending`). `step_rejected`: an event the snapshot does not accept (a
   timer firing with a stale token, an outcome without the wait of its call) changes nothing.
   `step_no_arrow`: an accepted event without an arrow only removes its wait.
   `step_pending_new`: after a step every wait is one of the snapshot's, other than the wait of
   the outcome handled, or one of the step itself (its token is the new `seq`).
   `step_records_wait`: every call of the reply has the token of the step, and a call of an
   action with outcomes records its wait. `step_keeps_wait`: **a step keeps every other wait**,
   whatever the move — only a newer call of the same action answered only at its latest call
   replaces it; `step_latest_replaces` says how.
2. **Each call is answered once**: `step_fresh`, `startWith_fresh` (no token of a wait or a timer
   exceeds `seq`, so an accepted outcome has an earlier token: `Snapshot.Fresh.accepts_le`) and
   `step_outcome_once` (after an outcome of a call, any outcome of the same
   call is ignored).
3. **Variants without a target**: `step_stay` (the variant's actions without a move, the timer
   firing consumed), `stay_keeps` (message, places, the place to go back to and the other
   timers stay) and `enterBot_stay` (on entering the bot such a variant leads to the initial
   message).
4. **Background actions do not affect the machine**: `step_undetach`, `startWith_undetach`,
   `recover_undetach` — the machine with every action in the foreground computes the same
   snapshots and the same reply up to the flag.
5. **The protocol**: `handle_restart` (`restart` is `recover` without the places, the place to
   go back to and the waits), `restart_pending` (after `restart` only the waits of the
   first-entry actions remain), `decEvent_key` (the arrow is looked up by exactly the event the
   host sent), `splitOutcome_outcomeEvent` and `decEvent_outcome` (an outcome event of a declared
   action is decoded as that outcome), `decStartEvent_ok` (`start` never takes a timer or an
   outcome event) and `startWith_outcome` (an outcome chooses nothing on the first entry).
-/

namespace Figram

/-! ## What the helpers of waits and variants without a target compute

The reference semantics `Chart.step` uses the helpers of `Figram/Core.lean` (`accepts`, `settle`,
`consume`, `addWait`, `stay`), so `compile_correct` cannot find an error in them; these theorems
state what they compute in terms of lists (a change of a helper changes a pinned statement). -/

theorem Snapshot.accepts_named (s : Snapshot) (e : EventId) : s.accepts (.named e) = true := rfl

theorem Snapshot.accepts_timer (s : Snapshot) (id : String) (tok : Nat) :
    s.accepts (.timer id tok) = decide ((s.timers.find? (fun p => decide (p.1 = id))).map (·.2) = some tok) :=
  rfl

theorem Snapshot.accepts_outcome (s : Snapshot) (c o : String) (tok : Nat) :
    s.accepts (.outcome c o tok) = decide ((c, tok) ∈ s.pending) := rfl

theorem Snapshot.settle_outcome (s : Snapshot) (c o : String) (tok : Nat) :
    s.settle (.outcome c o tok) = { s with pending := s.pending.filter (fun p => decide (p ≠ (c, tok))) } :=
  rfl

theorem Snapshot.consume_timer (s : Snapshot) (id : String) (tok : Nat) :
    s.consume (.timer id tok) = { s with timers := s.timers.filter (fun p => decide (p.1 ≠ id)) } := rfl

theorem Snapshot.consume_other (s : Snapshot) (ev : Event) (h : ∀ id t, ev ≠ .timer id t) :
    s.consume ev = s := by
  cases ev with
  | timer id t => exact absurd rfl (h id t)
  | _ => rfl

theorem addWait_free (pend : List (String × Nat)) (n : String) (tok : Nat) :
    addWait pend n tok .free = pend := rfl

theorem addWait_latest (pend : List (String × Nat)) (n : String) (tok : Nat) :
    addWait pend n tok .latest = (n, tok) :: pend.filter (fun p => decide (p.1 ≠ n)) := rfl

theorem addWait_every (pend : List (String × Nat)) (n : String) (tok : Nat) :
    addWait pend n tok .every = (n, tok) :: pend.filter (fun p => decide (p ≠ (n, tok))) := rfl

theorem stay_eq (s : Snapshot) (ev : Event) (acts : List Action) :
    stay s ev acts = realize (s.consume ev) s.state acts := rfl

theorem waitNames_nil : waitNames [] = [] := rfl

theorem waitNames_call (n : String) (d : Bool) (w : Wait) (tok : Nat) (acts : List Action) :
    waitNames (.call n d w tok :: acts) = (if w = .free then [] else [n]) ++ waitNames acts := by
  by_cases h : w = .free <;> simp [waitNames, h]

theorem waitNames_other (a : Action) (acts : List Action) (h : ∀ n d w tok, a ≠ .call n d w tok) :
    waitNames (a :: acts) = waitNames acts := by
  cases a with
  | call n d w tok => exact absurd rfl (h n d w tok)
  | _ => rfl

/-! ## Executing actions and the waits -/

theorem realizeOne_pending_mem (tok : Nat) (acc : Snapshot × List Action) (a : Action)
    {p : String × Nat} (h : p ∈ (realizeOne tok acc a).1.pending) :
    p ∈ acc.1.pending ∨ p.2 = tok := by
  cases a with
  | call n d w t =>
    rcases Engine.mem_addWait h with h | h
    · exact Or.inr (by rw [h])
    · exact Or.inl h
  | _ => exact Or.inl h

theorem foldl_pending_mem (tok : Nat) (acts : List Action) (acc : Snapshot × List Action)
    {p : String × Nat} (h : p ∈ (acts.foldl (realizeOne tok) acc).1.pending) :
    p ∈ acc.1.pending ∨ p.2 = tok := by
  induction acts generalizing acc with
  | nil => exact Or.inl h
  | cons a as ih =>
    rcases ih _ h with h | h
    · exact realizeOne_pending_mem tok acc a h
    · exact Or.inr h

/-- The waits after `realize`: those of the snapshot and those of the transition. -/
theorem realize_pending_mem (s : Snapshot) (T : NodeId) (acts : List Action)
    {p : String × Nat} (h : p ∈ (realize s T acts).1.pending) :
    p ∈ s.pending ∨ p.2 = s.seq + 1 :=
  foldl_pending_mem _ _ _ h

theorem realizeOne_timers_mem (tok : Nat) (acc : Snapshot × List Action) (a : Action)
    {p : String × Nat} (h : p ∈ (realizeOne tok acc a).1.timers) :
    p ∈ acc.1.timers ∨ p.2 = tok := by
  cases a with
  | startTimer id ms t =>
    rcases Engine.mem_assign h with h | h
    · exact Or.inr (by rw [h])
    · exact Or.inl h
  | cancelTimer id => exact Or.inl (Engine.mem_erase h)
  | _ => exact Or.inl h

theorem foldl_timers_mem (tok : Nat) (acts : List Action) (acc : Snapshot × List Action)
    {p : String × Nat} (h : p ∈ (acts.foldl (realizeOne tok) acc).1.timers) :
    p ∈ acc.1.timers ∨ p.2 = tok := by
  induction acts generalizing acc with
  | nil => exact Or.inl h
  | cons a as ih =>
    rcases ih _ h with h | h
    · exact realizeOne_timers_mem tok acc a h
    · exact Or.inr h

theorem realize_timers_mem (s : Snapshot) (T : NodeId) (acts : List Action)
    {p : String × Nat} (h : p ∈ (realize s T acts).1.timers) :
    p ∈ s.timers ∨ p.2 = s.seq + 1 :=
  foldl_timers_mem _ _ _ h

/-- The reply only grows while the actions are executed. -/
theorem foldl_out_mem (tok : Nat) (acts : List Action) (x : Snapshot) (out : List Action)
    {a : Action} (h : a ∈ out) : a ∈ (acts.foldl (realizeOne tok) (x, out)).2 := by
  rw [Machine.foldl_realize_out]
  exact List.mem_append_left _ h

/-- Within one transition (one token) a recorded wait stays. -/
theorem realizeOne_keep_tok (tok : Nat) (acc : Snapshot × List Action) (a : Action) {n : String}
    (h : (n, tok) ∈ acc.1.pending) : (n, tok) ∈ (realizeOne tok acc a).1.pending := by
  cases a with
  | call n' d w t =>
    show (n, tok) ∈ addWait acc.1.pending n' tok w
    cases w with
    | free => exact h
    | latest => by_cases hn : n = n' <;> simp [addWait, assign, hn, h]
    | every => by_cases hn : n = n' <;> simp [addWait, hn, h]
  | _ => exact h

theorem foldl_keep_tok (tok : Nat) (acts : List Action) (acc : Snapshot × List Action) {n : String}
    (h : (n, tok) ∈ acc.1.pending) : (n, tok) ∈ (acts.foldl (realizeOne tok) acc).1.pending := by
  induction acts generalizing acc with
  | nil => exact h
  | cons a as ih => exact ih _ (realizeOne_keep_tok tok acc a h)

theorem realizeOne_records (tok : Nat) (x : Snapshot) (out : List Action) (a : Action)
    {n : String} {d : Bool} {w : Wait} {t : Nat}
    (h : Action.call n d w t ∈ (realizeOne tok (x, out) a).2) :
    Action.call n d w t ∈ out ∨
      (t = tok ∧ (w ≠ .free → (n, tok) ∈ (realizeOne tok (x, out) a).1.pending)) := by
  cases a with
  | call n' d' w' t' =>
    simp only [realizeOne, List.mem_append, List.mem_singleton, Action.call.injEq] at h ⊢
    rcases h with h | ⟨rfl, rfl, rfl, rfl⟩
    · exact Or.inl h
    · refine Or.inr ⟨rfl, fun hw => ?_⟩
      cases w with
      | free => exact absurd rfl hw
      | latest => simp [addWait, assign]
      | every => simp [addWait]
  | remember sec n' => exact Or.inl h
  | _ => simp [realizeOne] at h; exact Or.inl h

theorem foldl_records (tok : Nat) (acts : List Action) (x : Snapshot) (out : List Action)
    {n : String} {d : Bool} {w : Wait} {t : Nat}
    (h : Action.call n d w t ∈ (acts.foldl (realizeOne tok) (x, out)).2) :
    Action.call n d w t ∈ out ∨
      (t = tok ∧ (w ≠ .free → (n, tok) ∈ (acts.foldl (realizeOne tok) (x, out)).1.pending)) := by
  induction acts generalizing x out with
  | nil => exact Or.inl h
  | cons a as ih =>
    simp only [List.foldl_cons] at h ⊢
    rcases ih _ _ h with h' | h'
    · rcases realizeOne_records tok x out a h' with h'' | ⟨ht, hw⟩
      · exact Or.inl h''
      · exact Or.inr ⟨ht, fun hw' => foldl_keep_tok tok as _ (hw hw')⟩
    · exact Or.inr h'

/-- **Every call of a transition has its token, and a call of an action with outcomes records
its wait.** -/
theorem realize_records (s : Snapshot) (T : NodeId) (acts : List Action)
    {n : String} {d : Bool} {w : Wait} {t : Nat}
    (h : Action.call n d w t ∈ (realize s T acts).2) :
    t = s.seq + 1 ∧ (w ≠ .free → (n, s.seq + 1) ∈ (realize s T acts).1.pending) := by
  rcases foldl_records (s.seq + 1) acts _ [] h with h | h
  · simp at h
  · exact h

theorem realizeOne_latest (tok : Nat) (x : Snapshot) (out : List Action) (a : Action)
    {n : String} {d : Bool} {t : Nat}
    (h : Action.call n d .latest t ∈ (realizeOne tok (x, out) a).2) :
    Action.call n d .latest t ∈ out ∨
      ∀ t', (n, t') ∈ (realizeOne tok (x, out) a).1.pending → t' = tok := by
  cases a with
  | call n' d' w' t' =>
    simp only [realizeOne, List.mem_append, List.mem_singleton, Action.call.injEq] at h ⊢
    rcases h with h | ⟨rfl, rfl, rfl, rfl⟩
    · exact Or.inl h
    · refine Or.inr fun t' ht' => ?_
      simp only [addWait, assign, List.mem_cons, Prod.mk.injEq, List.mem_filter,
        decide_eq_true_eq] at ht'
      rcases ht' with ⟨_, h⟩ | ⟨_, h⟩
      · exact h
      · exact absurd rfl h
  | remember sec n' => exact Or.inl h
  | _ => simp [realizeOne] at h; exact Or.inl h

theorem foldl_latest (tok : Nat) (acts : List Action) (x : Snapshot) (out : List Action)
    {n : String} {d : Bool} {t : Nat}
    (h : Action.call n d .latest t ∈ (acts.foldl (realizeOne tok) (x, out)).2) :
    Action.call n d .latest t ∈ out ∨
      ∀ t', (n, t') ∈ (acts.foldl (realizeOne tok) (x, out)).1.pending → t' = tok := by
  induction acts generalizing x out with
  | nil => exact Or.inl h
  | cons a as ih =>
    simp only [List.foldl_cons] at h ⊢
    rcases ih _ _ h with h' | h'
    · rcases realizeOne_latest tok x out a h' with h'' | hp
      · exact Or.inl h''
      · refine Or.inr fun t' ht' => ?_
        rcases foldl_pending_mem tok as _ ht' with h3 | h3
        · exact hp t' h3
        · exact h3
    · exact Or.inr h'

/-- **A call answered only at its latest call replaces the older waits of its action.** -/
theorem realize_latest (s : Snapshot) (T : NodeId) (acts : List Action)
    {n : String} {d : Bool} {t : Nat}
    (h : Action.call n d .latest t ∈ (realize s T acts).2) :
    ∀ t', (n, t') ∈ (realize s T acts).1.pending → t' = s.seq + 1 := by
  rcases foldl_latest (s.seq + 1) acts _ [] h with h | h
  · simp at h
  · exact h

theorem realizeOne_keep (tok : Nat) (x : Snapshot) (out : List Action) (a : Action)
    {p : String × Nat} (hp : p ∈ x.pending) (ht : p.2 ≠ tok)
    (hl : ∀ d t, Action.call p.1 d .latest t ∉ (realizeOne tok (x, out) a).2) :
    p ∈ (realizeOne tok (x, out) a).1.pending := by
  obtain ⟨pn, pt⟩ := p
  cases a with
  | call n d w t =>
    show (pn, pt) ∈ addWait x.pending n tok w
    cases w with
    | free => exact hp
    | latest =>
      by_cases hn : pn = n
      · subst hn
        exact absurd (by simp [realizeOne]) (hl d tok)
      · simp [addWait, assign, hn, hp]
    | every =>
      simp only [addWait, List.mem_cons, List.mem_filter, Prod.mk.injEq, decide_eq_true_eq]
      exact Or.inr ⟨hp, fun h => ht (congrArg Prod.snd h)⟩
  | _ => exact hp

theorem foldl_keep (tok : Nat) (acts : List Action) (x : Snapshot) (out : List Action)
    {p : String × Nat} (hp : p ∈ x.pending) (ht : p.2 ≠ tok)
    (hl : ∀ d t, Action.call p.1 d .latest t ∉ (acts.foldl (realizeOne tok) (x, out)).2) :
    p ∈ (acts.foldl (realizeOne tok) (x, out)).1.pending := by
  induction acts generalizing x out with
  | nil => exact hp
  | cons a as ih =>
    simp only [List.foldl_cons] at hl ⊢
    refine ih _ _ ?_ hl
    exact realizeOne_keep tok x out a hp ht fun d t h => hl d t (foldl_out_mem _ _ _ _ h)

/-- **`realize` keeps a wait** unless the transition calls its action again as a call answered
only at its latest call. -/
theorem realize_keep (s : Snapshot) (T : NodeId) (acts : List Action) {p : String × Nat}
    (hp : p ∈ s.pending) (ht : p.2 ≠ s.seq + 1)
    (hl : ∀ d t, Action.call p.1 d .latest t ∉ (realize s T acts).2) :
    p ∈ (realize s T acts).1.pending :=
  foldl_keep _ _ _ _ hp ht hl

/-! ## Fresh tokens -/

/-- No token of a wait or of a timer exceeds the counter: every token was given by an earlier
step of the snapshot. Every snapshot the engine produces is fresh (`step_fresh`,
`startWith_fresh`, `restart_fresh`). -/
def Snapshot.Fresh (s : Snapshot) : Prop :=
  (∀ p ∈ s.pending, p.2 ≤ s.seq) ∧ ∀ p ∈ s.timers, p.2 ≤ s.seq

/-- In a fresh snapshot an accepted outcome has a token given before the snapshot. -/
theorem Snapshot.Fresh.accepts_le {s : Snapshot} (hf : s.Fresh) {c o : String} {tok : Nat}
    (ha : s.accepts (.outcome c o tok) = true) : tok ≤ s.seq := by
  simp only [Snapshot.accepts, decide_eq_true_eq] at ha
  exact hf.1 _ ha

theorem realize_fresh (s : Snapshot) (T : NodeId) (acts : List Action) (h : s.Fresh) :
    (realize s T acts).1.Fresh := by
  rw [Snapshot.Fresh, Engine.realize_seq]
  refine ⟨fun p hp => ?_, fun p hp => ?_⟩
  · rcases realize_pending_mem s T acts hp with hp | hp
    · exact Nat.le_succ_of_le (h.1 p hp)
    · exact Nat.le_of_eq hp
  · rcases realize_timers_mem s T acts hp with hp | hp
    · exact Nat.le_succ_of_le (h.2 p hp)
    · exact Nat.le_of_eq hp

theorem settle_pending_mem {s : Snapshot} {ev : Event} {p : String × Nat}
    (h : p ∈ (s.settle ev).pending) : p ∈ s.pending := by
  cases ev with
  | outcome c o t => exact (List.mem_filter.mp h).1
  | _ => exact h

theorem consume_timers_mem {s : Snapshot} {ev : Event} {p : String × Nat}
    (h : p ∈ (s.consume ev).timers) : p ∈ s.timers := by
  cases ev with
  | timer id t => exact Engine.mem_erase h
  | _ => exact h

namespace Machine

theorem markBack_pending (m : Machine) (s : Snapshot) (T : NodeId) :
    (m.markBack s T).pending = s.pending := by
  unfold markBack; split <;> rfl

theorem markBack_seq (m : Machine) (s : Snapshot) (T : NodeId) :
    (m.markBack s T).seq = s.seq := by
  unfold markBack; split <;> rfl

/-- An event that is not accepted, or a firing of a timer the message does not own, removes no
wait. -/
theorem settle_of_rejected (s : Snapshot) (ev : Event) (exit : List Action)
    (h : (s.accepts ev && ownsTimer exit ev) = false) : s.settle ev = s := by
  cases ev with
  | outcome c o t =>
    apply settle_of_not_accepts
    simpa [ownsTimer] using h
  | _ => rfl

/-- **The shape of a step**: nothing but the removal of the wait of the event, or a transition
(`realize`) from the snapshot without that wait, with its other waits, its counter and some of
its timers. -/
theorem step_cases (m : Machine) (s : Snapshot) (ev : Event) (env : HostEnv) :
    m.step s ev env = (s.settle ev, []) ∨
      ∃ x T acts, x.pending = (s.settle ev).pending ∧ x.seq = s.seq ∧
        (∀ p ∈ x.timers, p ∈ s.timers) ∧ m.step s ev env = realize x T acts := by
  unfold step
  split
  · right
    rw [enterBot_eq_realize]
    split
    · exact ⟨_, _, _, rfl, by simp, fun p hp => by simpa using hp, rfl⟩
    · exact ⟨_, _, _, rfl, by simp, fun p hp => by simpa using hp, rfl⟩
  · split
    · rename_i h
      left
      rw [settle_of_rejected s ev (m.exitOf s.state)
      (by cases hh : (s.accepts ev && ownsTimer (m.exitOf s.state) ev) <;> simp_all)]
    · split
      · exact Or.inl rfl
      · split
        · exact Or.inl rfl
        · split
          · right
            exact ⟨_, _, _, by simp, by simp,
              fun p hp => by simpa using consume_timers_mem hp, rfl⟩
          · right
            rw [fire_eq_realize]
            exact ⟨_, _, _, markBack_pending .., by rw [markBack_seq]; simp,
              fun p hp => by rw [markBack_timers] at hp; simpa using hp, rfl⟩

/-- **An event the snapshot does not accept changes nothing**: a timer firing with a stale token,
an outcome without the wait of its call (a stale, repeated or foreign token). -/
theorem step_rejected (m : Machine) (s : Snapshot) (ev : Event) (env : HostEnv)
    (hs : s.state ∈ m.states) (ha : s.accepts ev = false) : m.step s ev env = (s, []) := by
  unfold step
  simp [hs, ha]

/-- **An accepted event without an arrow only removes its wait** (an outcome nobody handles is
ignored, but its call is answered). -/
theorem step_no_arrow (m : Machine) (s : Snapshot) (ev : Event) (env : HostEnv)
    (hs : s.state ∈ m.states) (hr : m.row s.state ev.key = none) :
    m.step s ev env = (s.settle ev, []) := by
  unfold step
  simp only [hs, not_true_eq_false, ↓reduceIte, hr]
  split
  · rename_i h
    rw [settle_of_rejected s ev (m.exitOf s.state)
      (by cases hh : (s.accepts ev && ownsTimer (m.exitOf s.state) ev) <;> simp_all)]
  · rfl

/-- **The waits after a step**: those of the snapshot except the wait of the outcome handled,
and those of the step itself (their token is the new counter). -/
theorem step_pending_new (m : Machine) (s : Snapshot) (ev : Event) (env : HostEnv)
    {p : String × Nat} (h : p ∈ (m.step s ev env).1.pending) :
    p ∈ (s.settle ev).pending ∨ p.2 = s.seq + 1 := by
  rcases step_cases m s ev env with he | ⟨x, T, acts, hp, hs, _, he⟩
  · rw [he] at h; exact Or.inl h
  · rw [he] at h
    rcases realize_pending_mem x T acts h with h | h
    · exact Or.inl (hp ▸ h)
    · exact Or.inr (hs ▸ h)

/-- **A step keeps the waits**: whatever the move (to another message, out of a section, into
recovery), every wait other than that of the outcome handled stays, unless the step calls the
same action again as a call answered only at its latest call. -/
theorem step_keeps_wait (m : Machine) (s : Snapshot) (ev : Event) (env : HostEnv)
    {p : String × Nat} (hp : p ∈ (s.settle ev).pending) (ht : p.2 ≠ s.seq + 1)
    (hl : ∀ d t, Action.call p.1 d .latest t ∉ (m.step s ev env).2) :
    p ∈ (m.step s ev env).1.pending := by
  rcases step_cases m s ev env with he | ⟨x, T, acts, hx, hs, _, he⟩
  · rw [he]; exact hp
  · rw [he] at hl ⊢
    exact realize_keep x T acts (hx ▸ hp) (hs ▸ ht) hl

/-- **Every call of the reply has the token of the step** (the new counter), **and a call of an
action with outcomes records its wait** with that token. -/
theorem step_records_wait (m : Machine) (s : Snapshot) (ev : Event) (env : HostEnv)
    {n : String} {d : Bool} {w : Wait} {t : Nat}
    (h : Action.call n d w t ∈ (m.step s ev env).2) :
    t = (m.step s ev env).1.seq ∧ (w ≠ .free → (n, t) ∈ (m.step s ev env).1.pending) := by
  rcases step_cases m s ev env with he | ⟨x, T, acts, _, _, _, he⟩
  · rw [he] at h; simp at h
  · rw [he] at h ⊢
    obtain ⟨ht, hw⟩ := realize_records x T acts h
    rw [Engine.realize_seq, ht]
    exact ⟨rfl, hw⟩

/-- **A call answered only at its latest call replaces the older waits of its action**: after
the step, its only wait is that of the step. -/
theorem step_latest_replaces (m : Machine) (s : Snapshot) (ev : Event) (env : HostEnv)
    {n : String} {d : Bool} {t : Nat} (h : Action.call n d .latest t ∈ (m.step s ev env).2)
    {t' : Nat} (h' : (n, t') ∈ (m.step s ev env).1.pending) : t' = t := by
  have ht := (step_records_wait m s ev env h).1
  rcases step_cases m s ev env with he | ⟨x, T, acts, _, _, _, he⟩
  · rw [he] at h; simp at h
  · rw [he] at h h' ht
    rw [ht, Engine.realize_seq]
    exact realize_latest x T acts h t' h'

/-- **A step keeps the tokens fresh.** -/
theorem step_fresh (m : Machine) (s : Snapshot) (ev : Event) (env : HostEnv) (h : s.Fresh) :
    (m.step s ev env).1.Fresh := by
  rcases step_cases m s ev env with he | ⟨x, T, acts, hx, hs, ht, he⟩
  · rw [he]
    exact ⟨fun p hp => by simpa using h.1 p (settle_pending_mem hp),
      fun p hp => by simpa using h.2 p (by simpa using hp)⟩
  · rw [he]
    apply realize_fresh
    exact ⟨fun p hp => by rw [hs]; exact h.1 p (settle_pending_mem (hx ▸ hp)),
      fun p hp => by rw [hs]; exact h.2 p (ht p hp)⟩

theorem enterBot_fresh (m : Machine) (s : Snapshot) (pre : List Action) (ev : Event)
    (env : HostEnv) (h : s.Fresh) : (m.enterBot s pre ev env).1.Fresh := by
  have hs : (s.settle ev).Fresh :=
    ⟨fun p hp => by simpa using h.1 p (settle_pending_mem hp),
      fun p hp => by simpa using h.2 p (by simpa using hp)⟩
  rw [enterBot_eq_realize]
  split <;> exact realize_fresh _ _ _ hs

/-- **The first entry gives fresh tokens.** -/
theorem startWith_fresh (m : Machine) (n : Nat) (ev : Event) (env : HostEnv) :
    (m.startWith n ev env).1.Fresh :=
  enterBot_fresh m _ [] ev env ⟨fun _ h => by simp at h, fun _ h => by simp at h⟩

theorem recover_fresh (m : Machine) (s : Snapshot) (h : s.Fresh) : (m.recover s).1.Fresh := by
  rw [recover_eq_realize]
  exact realize_fresh _ _ _ h

/-- **Each call is answered once**: after an outcome of a call with a token given before the
snapshot (as is every accepted one in a fresh snapshot, `Snapshot.Fresh.accepts_le`), any outcome of the
same call — the same one again or another — is ignored. -/
theorem step_outcome_once (m : Machine) (hwf : m.wf = true) (s : Snapshot) (c o o' : String)
    (tok : Nat) (env env' : HostEnv) (ht : tok ≤ s.seq) :
    m.step (m.step s (.outcome c o tok) env).1 (.outcome c o' tok) env' =
      ((m.step s (.outcome c o tok) env).1, []) := by
  apply step_rejected _ _ _ _ (step_state_valid m hwf s _ env)
  simp only [Snapshot.accepts, decide_eq_false_iff_not]
  intro h
  rcases step_pending_new m s _ env h with h | h
  · simp [Snapshot.settle] at h
  · simp at h; omega

/-! ## Variants without a target -/

/-- **A variant without a target** chosen on the snapshot's message: its actions without a move,
from the snapshot without the wait of the outcome handled and without the timer that fired. -/
theorem step_stay (m : Machine) (s : Snapshot) (ev : Event) (env : HostEnv) (r : Row) (b : FBranch)
    (hs : s.state ∈ m.states) (ha : s.accepts ev = true)
    (ho : ownsTimer (m.exitOf s.state) ev = true) (hr : m.row s.state ev.key = some r)
    (hb : r.branches.find? (fun b => guardOK env b.guard) = some b) (hst : b.target = .stay) :
    m.step s ev env = realize ((s.settle ev).consume ev) s.state b.actions := by
  unfold step
  simp only [hs, not_true_eq_false, ↓reduceIte, ha, ho, Bool.and_self, Bool.not_true,
    Bool.false_eq_true, hr, hb, hst]
  simp [stay]

theorem realizeOne_noMove (tok : Nat) (acc : Snapshot × List Action) (a : Action)
    (h : (∃ n d w t, a = .call n d w t) ∨ ∃ n, a = .send n) :
    (realizeOne tok acc a).1.resume = acc.1.resume ∧ (realizeOne tok acc a).1.timers = acc.1.timers := by
  rcases h with ⟨n, d, w, t, rfl⟩ | ⟨n, rfl⟩ <;> exact ⟨rfl, rfl⟩

theorem foldl_noMove (tok : Nat) (acts : List Action) (acc : Snapshot × List Action)
    (h : ∀ a ∈ acts, (∃ n d w t, a = .call n d w t) ∨ ∃ n, a = .send n) :
    (acts.foldl (realizeOne tok) acc).1.resume = acc.1.resume ∧
      (acts.foldl (realizeOne tok) acc).1.timers = acc.1.timers := by
  induction acts generalizing acc with
  | nil => exact ⟨rfl, rfl⟩
  | cons a as ih =>
    simp only [List.foldl_cons]
    obtain ⟨h1, h2⟩ := ih (realizeOne tok acc a) (fun x hx => h x (List.mem_cons_of_mem _ hx))
    obtain ⟨h3, h4⟩ := realizeOne_noMove tok acc a (h a List.mem_cons_self)
    exact ⟨h1.trans h3, h2.trans h4⟩

/-- **A variant without a target moves nothing**: with actions that only call the developer's
code or send messages (the actions of a variant in a document are calls), the message, the
remembered places and the place to go back to stay, the timers too except the one that fired
(it is consumed), and the counter grows by one. -/
theorem stay_keeps (s : Snapshot) (ev : Event) (acts : List Action)
    (h : ∀ a ∈ acts, (∃ n d w t, a = .call n d w t) ∨ ∃ n, a = .send n) :
    (stay s ev acts).1.state = s.state ∧ (stay s ev acts).1.resume = s.resume ∧
      (stay s ev acts).1.back = s.back ∧ (stay s ev acts).1.timers = (s.consume ev).timers ∧
      (stay s ev acts).1.seq = s.seq + 1 := by
  obtain ⟨h1, h2⟩ := foldl_noMove ((s.consume ev).seq + 1) acts
    ({ s.consume ev with state := s.state, seq := (s.consume ev).seq + 1 }, []) h
  refine ⟨by simp [stay, realize_state], ?_, by simp [stay, realize_back], ?_, ?_⟩
  · simp only [stay, realize]; rw [h1]; simp
  · simp only [stay, realize]; rw [h2]
  · simp [stay, Engine.realize_seq]

/-- **On entering the bot a variant without a target leads to the initial message** (recovery,
`start` with an event): its actions and the entry of the initial message. -/
theorem enterBot_stay (m : Machine) (s : Snapshot) (pre : List Action) (ev : Event) (env : HostEnv)
    (b : FBranch) (ha : s.accepts ev = true) (hb : m.choose m.initial ev env = some b)
    (hst : b.target = .stay) :
    m.enterBot s pre ev env =
      realize (s.settle ev) m.initial (pre ++ b.actions ++ m.entryOf m.initial) := by
  rw [enterBot_eq_realize_some m s pre ev env b ha hb, hst]
  rfl

/-- **An outcome chooses nothing on the first entry**: a new user waits for nothing. -/
theorem startWith_outcome (m : Machine) (n : Nat) (c o : String) (t : Nat) (env : HostEnv) :
    m.startWith n (.outcome c o t) env =
      realize ⟨"", [], [], n, none, []⟩ m.initial m.initActions := by
  unfold startWith
  rw [enterBot_eq_realize_none _ _ _ _ _ (Or.inl (by simp [Snapshot.accepts]))]
  rfl

end Machine

/-! ## Background actions do not affect the machine -/

/-- The action in the foreground. -/
def Action.undetach : Action → Action
  | .call n _ w t => .call n false w t
  | a => a

def FBranch.undetach (b : FBranch) : FBranch := { b with actions := b.actions.map Action.undetach }

def Row.undetach (r : Row) : Row := { r with branches := r.branches.map FBranch.undetach }

/-- The machine with every action in the foreground. -/
def Machine.undetach (m : Machine) : Machine :=
  { m with
    initActions := m.initActions.map Action.undetach
    table := m.table.map fun e => (e.1, e.2.map Row.undetach)
    entry := m.entry.map fun e => (e.1, e.2.map Action.undetach)
    exit := m.exit.map fun e => (e.1, e.2.map Action.undetach) }

theorem lookup_map_val {β γ} (l : List (String × β)) (f : β → γ) (k : String) :
    lookup (l.map fun p => (p.1, f p.2)) k = (lookup l k).map f := by
  induction l with
  | nil => rfl
  | cons p l ih =>
    by_cases h : p.1 = k <;> simp [lookup, h] at ih ⊢ <;> exact ih

namespace Action

@[simp] theorem undetach_cancelTimer (id : String) : (Action.cancelTimer id).undetach = .cancelTimer id :=
  rfl

theorem undetach_eq_cancel_iff (a : Action) (id : String) :
    a.undetach = .cancelTimer id ↔ a = .cancelTimer id := by
  cases a <;> simp [undetach]

theorem contains_undetach (l : List Action) (id : String) :
    (l.map undetach).contains (.cancelTimer id) = l.contains (.cancelTimer id) := by
  apply Bool.eq_iff_iff.mpr
  simp [undetach_eq_cancel_iff]

theorem isCancel_undetach (a : Action) : a.undetach.isCancel = a.isCancel := by
  cases a <;> rfl

theorem map_undetach_cancels (l : List Action) (h : ∀ a ∈ l, a.isCancel = true) :
    l.map undetach = l := by
  induction l with
  | nil => rfl
  | cons a l ih =>
    simp only [List.map_cons]
    have ha := h a List.mem_cons_self
    rw [ih (fun b hb => h b (List.mem_cons_of_mem _ hb))]
    cases a <;> simp_all [isCancel, undetach]

end Action

theorem staleCancels_undetach (exit : List Action) (s : Snapshot) :
    staleCancels (exit.map Action.undetach) s = staleCancels exit s := by
  simp only [staleCancels, Action.contains_undetach]

theorem ownsTimer_undetach (exit : List Action) (ev : Event) :
    ownsTimer (exit.map Action.undetach) ev = ownsTimer exit ev := by
  cases ev <;> simp [ownsTimer, Action.undetach_eq_cancel_iff]

theorem realizeOne_undetach (tok : Nat) (x : Snapshot) (out : List Action) (a : Action) :
    realizeOne tok (x, out.map Action.undetach) a.undetach =
      ((realizeOne tok (x, out) a).1, (realizeOne tok (x, out) a).2.map Action.undetach) := by
  cases a <;> simp [realizeOne, Action.undetach]

theorem foldl_undetach (tok : Nat) (acts : List Action) (x : Snapshot) (out : List Action) :
    (acts.map Action.undetach).foldl (realizeOne tok) (x, out.map Action.undetach) =
      ((acts.foldl (realizeOne tok) (x, out)).1,
        (acts.foldl (realizeOne tok) (x, out)).2.map Action.undetach) := by
  induction acts generalizing x out with
  | nil => rfl
  | cons a as ih =>
    simp only [List.map_cons, List.foldl_cons, realizeOne_undetach]
    exact ih _ _

/-- **Executing the actions in the foreground** gives the same snapshot and the same reply up to
the flag. -/
theorem realize_undetach (s : Snapshot) (T : NodeId) (acts : List Action) :
    realize s T (acts.map Action.undetach) =
      ((realize s T acts).1, (realize s T acts).2.map Action.undetach) := by
  have h := foldl_undetach (s.seq + 1) acts { s with state := T, seq := s.seq + 1 } []
  simpa [realize] using h

namespace Machine

theorem undetach_entryOf (m : Machine) (n : NodeId) :
    m.undetach.entryOf n = (m.entryOf n).map Action.undetach := by
  simp only [entryOf, Machine.undetach, lookup_map_val]
  cases lookup m.entry n <;> rfl

theorem undetach_exitOf (m : Machine) (n : NodeId) :
    m.undetach.exitOf n = (m.exitOf n).map Action.undetach := by
  simp only [exitOf, Machine.undetach, lookup_map_val]
  cases lookup m.exit n <;> rfl

theorem undetach_row (m : Machine) (n : NodeId) (e : EventId) :
    m.undetach.row n e = (m.row n e).map Row.undetach := by
  simp only [row, Machine.undetach, lookup_map_val]
  cases lookup m.table n with
  | none => rfl
  | some rs =>
    simp only [Option.map_some, Option.getD_some, List.find?_map]
    rfl

theorem find?_undetach (bs : List FBranch) (env : HostEnv) :
    (bs.map FBranch.undetach).find? (fun b => guardOK env b.guard) =
      (bs.find? (fun b => guardOK env b.guard)).map FBranch.undetach := by
  rw [List.find?_map]
  rfl

theorem undetach_resolve (m : Machine) (s : Snapshot) (t : Target) :
    m.undetach.resolve s t = m.resolve s t := by
  induction t with
  | back fb ih => simp only [resolve, ih]; rfl
  | _ => rfl

theorem undetach_choose (m : Machine) (n : NodeId) (ev : Event) (env : HostEnv) :
    m.undetach.choose n ev env = (m.choose n ev env).map FBranch.undetach := by
  unfold choose
  cases ev with
  | timer => rfl
  | _ =>
    simp only [undetach_row]
    cases m.row n _ with
    | none => rfl
    | some r => simp only [Option.map_some, Row.undetach, find?_undetach]

theorem undetach_fire (m : Machine) (s : Snapshot) (b : FBranch) :
    m.undetach.fire s b.undetach = ((m.fire s b).1, (m.fire s b).2.map Action.undetach) := by
  rw [fire_eq_realize, fire_eq_realize]
  have hT : m.undetach.resolve s b.undetach.target = m.resolve s b.target := undetach_resolve m s _
  have hst : (staleCancels (m.exitOf s.state) s).map Action.undetach =
      staleCancels (m.exitOf s.state) s :=
    Action.map_undetach_cancels _ (staleCancels_cancel _ _)
  rw [hT, undetach_exitOf, undetach_entryOf, staleCancels_undetach, ← realize_undetach]
  simp only [List.map_append, hst]
  rfl

theorem undetach_enterBot (m : Machine) (s : Snapshot) (pre : List Action) (ev : Event)
    (env : HostEnv) (hpre : pre.map Action.undetach = pre) :
    m.undetach.enterBot s pre ev env =
      ((m.enterBot s pre ev env).1, (m.enterBot s pre ev env).2.map Action.undetach) := by
  rw [enterBot_eq_realize, enterBot_eq_realize]
  have hi : m.undetach.initial = m.initial := rfl
  have hia : m.undetach.initActions = m.initActions.map Action.undetach := rfl
  simp only [hi, hia, undetach_choose]
  by_cases ha : s.accepts ev = true
  · simp only [ha, ↓reduceIte]
    cases hc : m.choose m.initial ev env with
    | none =>
      simp only [Option.map_none]
      rw [← realize_undetach]
      simp only [List.map_append, hpre]
    | some b =>
      simp only [Option.map_some, FBranch.undetach]
      rw [undetach_resolve, undetach_entryOf, ← realize_undetach]
      simp only [List.map_append, hpre]
  · simp only [Bool.not_eq_true] at ha
    simp only [ha, Bool.false_eq_true, ↓reduceIte]
    rw [← realize_undetach]
    simp only [List.map_append, hpre]

theorem map_undetach_timerCancels (s : Snapshot) :
    (s.timers.map (fun p => Action.cancelTimer p.1)).map Action.undetach =
      s.timers.map (fun p => Action.cancelTimer p.1) := by
  simp [List.map_map, Function.comp_def]

/-- **Background actions do not affect a step**: the machine with every action in the foreground
gives the same snapshot and the same reply up to the flag. -/
theorem step_undetach (m : Machine) (s : Snapshot) (ev : Event) (env : HostEnv) :
    m.undetach.step s ev env = ((m.step s ev env).1, (m.step s ev env).2.map Action.undetach) := by
  unfold step
  rw [show m.undetach.states = m.states from rfl]
  split
  · exact undetach_enterBot m s _ ev env (map_undetach_timerCancels s)
  · rw [undetach_exitOf, ownsTimer_undetach]
    split
    · rfl
    · rw [undetach_row]
      cases m.row s.state ev.key with
      | none => rfl
      | some r =>
        simp only [Option.map_some, Row.undetach, find?_undetach]
        cases r.branches.find? (fun b => guardOK env b.guard) with
        | none => rfl
        | some b =>
          simp only [Option.map_some]
          rw [show b.undetach.target = b.target from rfl]
          split
          · simp only [stay]
            exact realize_undetach _ _ _
          · exact undetach_fire m _ b

/-- **Background actions do not affect the first entry.** -/
theorem startWith_undetach (m : Machine) (n : Nat) (ev : Event) (env : HostEnv) :
    m.undetach.startWith n ev env =
      ((m.startWith n ev env).1, (m.startWith n ev env).2.map Action.undetach) :=
  undetach_enterBot m _ [] ev env rfl

/-- **Background actions do not affect `restart`.** -/
theorem recover_undetach (m : Machine) (s : Snapshot) :
    m.undetach.recover s = ((m.recover s).1, (m.recover s).2.map Action.undetach) := by
  rw [recover_eq_realize, recover_eq_realize,
    show m.undetach.initActions = m.initActions.map Action.undetach from rfl,
    show m.undetach.initial = m.initial from rfl, ← realize_undetach]
  simp only [List.map_append, map_undetach_timerCancels]

end Machine

/-! ## Outcome events -/

theorem dropWhile_prefix {p : Char → Bool} {l r : List Char} {c : Char} (hl : ∀ x ∈ l, p x = true)
    (hc : p c = false) : (l ++ c :: r).dropWhile p = c :: r := by
  induction l with
  | nil => simp [hc]
  | cons x xs ih =>
    simp only [List.cons_append, List.dropWhile_cons, hl x List.mem_cons_self, ↓reduceIte]
    exact ih (fun y hy => hl y (List.mem_cons_of_mem _ hy))

theorem takeWhile_prefix {p : Char → Bool} {l r : List Char} {c : Char} (hl : ∀ x ∈ l, p x = true)
    (hc : p c = false) : (l ++ c :: r).takeWhile p = l := by
  induction l with
  | nil => simp [hc]
  | cons x xs ih =>
    simp only [List.cons_append, List.takeWhile_cons, hl x List.mem_cons_self, ↓reduceIte]
    rw [ih (fun y hy => hl y (List.mem_cons_of_mem _ hy))]

theorem toList_reverse_isEmpty (s : String) (hs : s ≠ "") : s.toList.reverse.isEmpty = false := by
  cases hsl : s.toList with
  | nil => exact absurd (String.toList_injective (by simp [hsl])) hs
  | cons x xs => simp

/-- **The event of an outcome splits back** into the action and the outcome (an outcome name has
no `:`). -/
theorem splitOutcome_outcomeEvent (c o : String) (hc : c ≠ "") (ho : o ≠ "")
    (hco : ':' ∉ o.toList) :
    splitOutcome (c ++ ":" ++ o) = some (c, o) := by
  have hl : ∀ x ∈ o.toList.reverse, (x != ':') = true := by
    intro x hx
    simp only [List.mem_reverse] at hx
    simp only [bne_iff_ne, ne_eq]
    rintro rfl
    exact hco hx
  have hcol : (':' != ':') = false := by decide
  have hrev : (c ++ ":" ++ o).toList.reverse = o.toList.reverse ++ ':' :: c.toList.reverse := by
    simp [String.toList_append]
  unfold splitOutcome
  simp only [hrev, dropWhile_prefix hl hcol, takeWhile_prefix hl hcol, toList_reverse_isEmpty c hc,
    toList_reverse_isEmpty o ho, Bool.or_self, Bool.false_eq_true, ↓reduceIte, List.reverse_reverse,
    String.ofList_toList]

/-- What `splitOutcome` splits joins back to the string. -/
theorem splitOutcome_join {x c o : String} (h : splitOutcome x = some (c, o)) :
    c ++ ":" ++ o = x := by
  unfold splitOutcome at h
  simp only at h
  generalize hd : x.toList.reverse.dropWhile (· != ':') = rest at h
  cases rest with
  | nil => simp at h
  | cons y ys =>
    simp only at h
    split at h
    · simp at h
    · simp only [Option.some.injEq, Prod.mk.injEq] at h
      obtain ⟨rfl, rfl⟩ := h
      have hy : (y != ':') = false := by
        have := List.head_dropWhile_not (fun c => c != ':') (l := x.toList.reverse) (by rw [hd]; simp)
        simpa [hd] using this
      have hy' : y = ':' := by simpa using hy
      subst hy'
      apply String.toList_injective
      have hj := List.takeWhile_append_dropWhile (p := (fun c => c != ':')) (l := x.toList.reverse)
      rw [hd] at hj
      have := congrArg List.reverse hj
      simp only [List.reverse_append, List.reverse_reverse, List.reverse_cons] at this
      simp only [String.toList_append, String.toList_ofList, show ":".toList = [':'] from rfl]
      exact this

end Figram

namespace Figram.Engine
open Figram.Format

theorem startsWith_join {p name : String} {n : Nat} (hn : p.length = n) (h : name.startsWith p = true) :
    p ++ (name.drop n).toString = name := by
  apply String.toList_injective
  obtain ⟨t, ht⟩ := String.startsWith_string_iff.mp h
  subst hn
  simp only [String.Slice.toString_eq, String.toList_append, String.toList_copy_drop]
  rw [← ht]
  simp [← String.length_toList]

/-- **The arrow is looked up by exactly the event the host sent**: the key of a decoded event is
the request's `event`. -/
theorem decEvent_key (kvs : Obj) (ev : Event) (h : decEvent kvs = .ok ev) :
    get kvs "event" = some (.str ev.key) := by
  unfold decEvent at h
  cases he : get kvs "event" with
  | none => simp only [he] at h; cases h
  | some j =>
    cases j with
    | str e =>
      simp only [he] at h
      cases h0 : e.isEmpty
      · simp only [h0, Bool.false_eq_true, ↓reduceIte, bind, Except.bind, pure, Except.pure] at h
        cases ht : e.startsWith "timer:"
        · simp only [ht, Bool.false_eq_true, ↓reduceIte] at h
          cases hc : e.startsWith "call:"
          · simp only [hc, Bool.false_eq_true, ↓reduceIte] at h
            cases hk : (get kvs "token").isSome
            · simp only [hk, Bool.false_eq_true, ↓reduceIte] at h
              cases h
              rfl
            · simp only [hk, ↓reduceIte] at h
              cases h
          · simp only [hc, ↓reduceIte] at h
            cases hs : splitOutcome (e.drop 5).toString with
            | none => simp only [hs] at h; cases h
            | some co =>
              obtain ⟨c, o⟩ := co
              simp only [hs] at h
              cases hk : get kvs "token" with
              | none => simp only [hk] at h; cases h
              | some j =>
                simp only [hk] at h
                cases hn : reqNat "token" j with
                | error => simp only [hn] at h; cases h
                | ok t =>
                  simp only [hn] at h
                  cases h
                  simp only [Event.key, outcomeEvent]
                  rw [show "call:" ++ c ++ ":" ++ o = "call:" ++ (c ++ ":" ++ o) by
                      simp [String.append_assoc],
                    splitOutcome_join hs, startsWith_join (by decide) hc]
        · simp only [ht, ↓reduceIte] at h
          cases hi : (e.drop 6).toString.isEmpty
          · simp only [hi, Bool.false_eq_true, ↓reduceIte] at h
            cases hk : get kvs "token" with
            | none => simp only [hk] at h; cases h
            | some j =>
              simp only [hk] at h
              cases hn : reqNat "token" j with
              | error => simp only [hn] at h; cases h
              | ok t =>
                simp only [hn] at h
                cases h
                simp only [Event.key, timerEvent]
                rw [startsWith_join (by decide) ht]
          · simp only [hi, ↓reduceIte] at h
            cases h
      · simp only [h0, ↓reduceIte] at h
        cases h
    | _ => simp only [he] at h; cases h

/-- **`start` never takes a timer or an outcome event**: a new user has no timers and no waits. -/
theorem decStartEvent_ok (kvs : Obj) (e : EventId) (h : decStartEvent kvs = .ok e) :
    get kvs "event" = some (.str e) ∧ e ≠ "" ∧ e.startsWith "timer:" = false ∧
      e.startsWith "call:" = false := by
  unfold decStartEvent at h
  split at h
  · rename_i e' he
    split at h
    · simp at h
    · rename_i h1
      split at h
      · simp at h
      · rename_i h2
        split at h
        · simp at h
        · rename_i h3
          simp only [pure, Except.pure, Except.ok.injEq] at h
          subst h
          refine ⟨he, ?_, by simpa using h2, by simpa using h3⟩
          intro h0; subst h0; simp at h1
  · simp at h

/-- **An outcome event of an action is decoded as that outcome** (a non-empty action name, an
outcome name without `:`, a token within the bounds). -/
theorem decEvent_outcome (kvs : Obj) (c o : String) (tok : Nat) (hc : c ≠ "") (ho : o ≠ "")
    (hco : ':' ∉ o.toList) (htok : tok ≤ maxInt)
    (hev : get kvs "event" = some (.str (outcomeEvent c o)))
    (ht : get kvs "token" = some (.num tok 0)) :
    decEvent kvs = .ok (.outcome c o tok) := by
  have hdrop : ((outcomeEvent c o).drop 5).toString = c ++ ":" ++ o := by
    apply String.toList_injective
    simp [outcomeEvent, String.toList_append, String.toList_copy_drop]
  have htimer : (outcomeEvent c o).startsWith "timer:" = false := by
    simp [outcomeEvent, String.startsWith_string_eq_false_iff, String.toList_append, List.IsPrefix]
  have hcall : (outcomeEvent c o).startsWith "call:" = true := by
    simp [outcomeEvent, String.startsWith_string_iff, String.toList_append]
  have hne : (outcomeEvent c o).isEmpty = false := by
    simp [outcomeEvent]
  have hreq : reqNat "token" (.num tok 0) = .ok tok := by
    simp [reqNat, numToNat_nat, htok]; rfl
  simp only [decEvent, hev, hne, htimer, hcall, hdrop, splitOutcome_outcomeEvent c o hc ho hco, ht,
    hreq, Bool.false_eq_true, ↓reduceIte, bind, Except.bind, pure, Except.pure]

/-- **`restart` is `recover`** from the snapshot without the remembered places, the place to go
back to and the waits (if the snapshot is decoded and the counter does not overflow). -/
theorem handle_restart (m : Machine) (kvs : Obj) (j : J) (s : Snapshot)
    (hop : get kvs "op" = some (.str "restart")) (hsn : get kvs "snapshot" = some j)
    (hj : decSnapshot j = .ok s)
    (hseq : (m.recover { s with resume := [], back := none, pending := [] }).1.seq ≤ maxInt) :
    handleObj m kvs = .ok (result ((get kvs "id").getD .null)
      (m.recover { s with resume := [], back := none, pending := [] }).1
      (m.recover { s with resume := [], back := none, pending := [] }).2 false) := by
  have hle : ¬ (m.recover { s with resume := [], back := none, pending := [] }).1.seq > maxInt := by
    omega
  simp only [handleObj, hop, hsn, hj, withCode, Except.mapError, bind, Except.bind, pure,
    Except.pure]
  simp [hle]

/-- **`restart` clears the waits**: after it only the waits of the first-entry actions remain
(their token is the new counter); late outcomes of earlier calls are ignored. -/
theorem restart_pending (m : Machine) (s : Snapshot) {p : String × Nat}
    (h : p ∈ (m.recover { s with resume := [], back := none, pending := [] }).1.pending) :
    p.2 = (m.recover { s with resume := [], back := none, pending := [] }).1.seq := by
  rw [Machine.recover_eq_realize] at h ⊢
  rw [realize_seq]
  rcases realize_pending_mem _ _ _ h with h | h
  · simp at h
  · exact h

theorem restart_fresh (m : Machine) (s : Snapshot) (h : s.Fresh) :
    (m.recover { s with resume := [], back := none, pending := [] }).1.Fresh :=
  Machine.recover_fresh m _ ⟨fun _ hp => by simp at hp, h.2⟩

end Figram.Engine

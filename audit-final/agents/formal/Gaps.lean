/-
Audit 5 (formal), item 4: claimed properties of 0.3.0 without a theorem in Figram/*.lean,
proved here in a scratch file (no product code changed).
Run: lake env lean /home/user/figram-audit/agents5/formal/Gaps.lean
  G1  the engine never writes the same wait twice (`pending` stays duplicate-free)
      — docs/format.md §8 "User snapshot": "The same wait listed twice (the engine never writes that)"
  G2  waits survive a document update: under EVERY machine (any document version, also one in which
      the action is gone or lost its outcomes) the outcome of a wait in the snapshot is accepted and
      removes the wait — docs/format.md §8, docs/host-contract.md §6
  G3  a timer firing handled by a variant without a target is consumed: its redelivery is ignored
      — docs/format.md §7 decision 13, docs/host-contract.md §3
  G4  Chart level (reference semantics): an accepted outcome without a matching variant (no arrow,
      or every condition false) is ignored but removes its wait — decision 12, rule 4 of a step
  G5  a decoded named event never has the key of an outcome event (nor of a timer event)
      — docs/format.md §4 "the prefixes `timer:` and `call:` are reserved"
  G6  calls of two consecutive steps never share a token (tokens strictly increase)
  G7  given V6 on the chart, a step calls each action with outcomes at most once
      (Chart.step and, through compile_correct, Machine.step) — docs/format.md §3 V6
-/
import Figram
import FigramAudit.Glue
open Figram Figram.Format Figram.Engine

namespace Audit5.Gaps

/-! ## G1: `pending` stays duplicate-free -/

theorem filter_nodup {α} {l : List α} (p : α → Bool) (h : l.Nodup) : (l.filter p).Nodup :=
  List.Pairwise.sublist List.filter_sublist h

theorem addWait_nodup {pend : List (String × Nat)} (h : pend.Nodup) (n : String) (tok : Nat)
    (w : Wait) : (addWait pend n tok w).Nodup := by
  cases w with
  | free => exact h
  | latest =>
    simp only [addWait, assign]
    exact List.nodup_cons.mpr ⟨by simp, filter_nodup _ h⟩
  | every =>
    simp only [addWait]
    exact List.nodup_cons.mpr ⟨by simp, filter_nodup _ h⟩

theorem realizeOne_nodup (tok : Nat) (acc : Snapshot × List Action) (a : Action)
    (h : acc.1.pending.Nodup) : (realizeOne tok acc a).1.pending.Nodup := by
  cases a with
  | call n d w t => exact addWait_nodup h n tok w
  | _ => exact h

theorem foldl_nodup (tok : Nat) (acts : List Action) (acc : Snapshot × List Action)
    (h : acc.1.pending.Nodup) : (acts.foldl (realizeOne tok) acc).1.pending.Nodup := by
  induction acts generalizing acc with
  | nil => exact h
  | cons a as ih => exact ih _ (realizeOne_nodup tok acc a h)

theorem realize_nodup (s : Snapshot) (T : NodeId) (acts : List Action) (h : s.pending.Nodup) :
    (realize s T acts).1.pending.Nodup :=
  foldl_nodup _ _ _ h

theorem settle_nodup (s : Snapshot) (ev : Event) (h : s.pending.Nodup) :
    (s.settle ev).pending.Nodup := by
  cases ev with
  | outcome c o t => exact filter_nodup _ h
  | _ => exact h

/-- **G1a. A step never writes the same wait twice** (from a snapshot without duplicates). -/
theorem step_nodup (m : Machine) (s : Snapshot) (ev : Event) (env : HostEnv)
    (h : s.pending.Nodup) : (m.step s ev env).1.pending.Nodup := by
  rcases Machine.step_cases m s ev env with he | ⟨x, T, acts, hx, _, _, he⟩
  · rw [he]; exact settle_nodup s ev h
  · rw [he]; exact realize_nodup x T acts (hx ▸ settle_nodup s ev h)

/-- **G1b. The first entry (with or without an event) has no duplicate waits.** -/
theorem startWith_nodup (m : Machine) (n : Nat) (ev : Event) (env : HostEnv) :
    (m.startWith n ev env).1.pending.Nodup := by
  unfold Machine.startWith
  rw [Machine.enterBot_eq_realize]
  have h0 : (Snapshot.settle ⟨"", [], [], n, none, []⟩ ev).pending.Nodup :=
    settle_nodup _ ev List.nodup_nil
  split <;> exact realize_nodup _ _ _ h0

/-- **G1c. `restart` has no duplicate waits** (whatever the snapshot sent). -/
theorem restart_nodup (m : Machine) (s : Snapshot) :
    (m.recover { s with resume := [], back := none, pending := [] }).1.pending.Nodup := by
  rw [Machine.recover_eq_realize]
  exact realize_nodup _ _ _ List.nodup_nil

/-- **G1d. Over a whole session** the waits stay duplicate-free. -/
theorem run_nodup (m : Machine) (s : Snapshot) (inp : List Input) (h : s.pending.Nodup) :
    (m.run s inp).1.pending.Nodup := by
  induction inp generalizing s with
  | nil => exact h
  | cons x rest ih =>
    obtain ⟨e, env⟩ := x
    exact ih _ (step_nodup m s e env h)

/-! ## G2: waits survive a document update -/

/-- **G2. Under every machine** — every document version, including one where the action was
removed or lost its outcomes — an outcome with the token of a wait in a fresh snapshot is accepted:
its wait is removed, so the step is not the "ignored" `(s, [])`. -/
theorem outcome_accepted_any_version (m : Machine) (s : Snapshot) (c o : String) (tok : Nat)
    (env : HostEnv) (hp : (c, tok) ∈ s.pending) (ht : tok ≤ s.seq) :
    (c, tok) ∉ (m.step s (.outcome c o tok) env).1.pending ∧
      m.step s (.outcome c o tok) env ≠ (s, []) := by
  have h1 : (c, tok) ∉ (m.step s (.outcome c o tok) env).1.pending := by
    intro h
    rcases Machine.step_pending_new m s _ env h with h | h
    · simp [Snapshot.settle] at h
    · simp at h; omega
  exact ⟨h1, fun he => h1 (by rw [he]; exact hp)⟩

/-- G2b: and if the new version has no arrow for it, only the wait goes. -/
theorem outcome_no_arrow_any_version (m : Machine) (s : Snapshot) (c o : String) (tok : Nat)
    (env : HostEnv) (hs : s.state ∈ m.states) (hr : m.row s.state (outcomeEvent c o) = none) :
    m.step s (.outcome c o tok) env =
      ({ s with pending := s.pending.filter (fun p => decide (p ≠ (c, tok))) }, []) :=
  Machine.step_no_arrow m s _ env hs hr

/-! ## G3: a timer consumed by a variant without a target -/

/-- **G3. A timer firing handled without a move is consumed**: from a fresh snapshot, a
redelivery of the same firing is ignored (whatever the variant's actions, even if they start
the same timer again: the new run has the new token). -/
theorem stay_timer_redelivery (m : Machine) (s : Snapshot) (id : String) (tok : Nat)
    (env env' : HostEnv) (r : Row) (b : FBranch) (hf : s.Fresh) (hs : s.state ∈ m.states)
    (ha : s.accepts (.timer id tok) = true) (ho : ownsTimer (m.exitOf s.state) (.timer id tok) = true)
    (hr : m.row s.state (Event.timer id tok).key = some r)
    (hb : r.branches.find? (fun b => guardOK env b.guard) = some b) (hst : b.target = .stay) :
    m.step (m.step s (.timer id tok) env).1 (.timer id tok) env' =
      ((m.step s (.timer id tok) env).1, []) := by
  rw [Machine.step_stay m s _ env r b hs ha ho hr hb hst]
  apply Machine.step_rejected
  · rw [realize_state]; exact hs
  · simp only [Snapshot.accepts, decide_eq_false_iff_not]
    intro hl
    have hm := lookup_some_mem hl
    have hle : tok ≤ s.seq := hf.2 (id, tok) (lookup_some_mem (by simpa [Snapshot.accepts] using ha))
    rcases realize_timers_mem _ _ _ hm with h | h
    · simp [Snapshot.consume, Snapshot.settle, erase] at h
    · simp [Snapshot.consume, Snapshot.settle] at h; omega

/-- Contrast (decision 6, host-contract §3): an accepted firing WITHOUT an arrow keeps the timer,
so a redelivery is accepted again. -/
theorem no_arrow_timer_redelivery_accepted (m : Machine) (s : Snapshot) (id : String) (tok : Nat)
    (env : HostEnv) (hs : s.state ∈ m.states) (hr : m.row s.state (Event.timer id tok).key = none) :
    (m.step s (.timer id tok) env).1 = s :=
  by rw [Machine.step_no_arrow m s _ env hs hr]; rfl

/-! ## G4: Chart level — an accepted outcome without a matching variant -/

/-- **G4. In the reference semantics**, an outcome with the token of a wait on a message whose
variants for it (with those of its sections) are all absent or false is ignored, and its wait is
removed. -/
theorem chart_outcome_unmatched (c : Chart) (s : Snapshot) (a o : String) (tok : Nat)
    (env : HostEnv) (hs : s.state ∈ c.leafIds) (hp : (a, tok) ∈ s.pending)
    (hn : (c.cands s.state (outcomeEvent a o)).find? (fun b => guardOK env b.guard) = none) :
    c.step s (.outcome a o tok) env =
      ({ s with pending := s.pending.filter (fun p => decide (p ≠ (a, tok))) }, []) := by
  have hs' : ¬ s.state ∉ c.leafIds := fun h => h hs
  simp only [Chart.step, hs', ↓reduceIte, Snapshot.accepts, ownsTimer, Event.key, hn]
  simp [hp, Snapshot.settle]

/-! ## G5: decoded named events never have an outcome or timer key -/

theorem startsWith_append (p q : String) : (p ++ q).startsWith p = true :=
  String.startsWith_string_iff.mpr ⟨q.toList, by simp⟩

/-- **G5.** A named event decoded by the engine is never `call:<action>:<outcome>` or
`timer:<name>`: arrows of outcomes and timers can only be reached with a token. -/
theorem decEvent_named_key {kvs : Format.Obj} {n : String}
    (h : decEvent kvs = .ok (.named n)) (c o id : String) :
    n ≠ outcomeEvent c o ∧ n ≠ timerEvent id := by
  obtain ⟨ht, hc⟩ := Figram.Audit.decEvent_named h
  constructor
  · rintro rfl
    have : (outcomeEvent c o).startsWith "call:" = true := by
      unfold outcomeEvent
      rw [String.append_assoc, String.append_assoc]
      exact startsWith_append _ _
    rw [this] at hc; cases hc
  · rintro rfl
    rw [timerEvent, startsWith_append] at ht; cases ht

/-! ## G6: tokens of consecutive steps -/

/-- **G6.** A call of a step and a call of the next step never carry the same token: the later
token is greater by one. -/
theorem consecutive_tokens (m : Machine) (s : Snapshot) (ev ev' : Event) (env env' : HostEnv)
    {n n' : String} {d d' : Bool} {w w' : Wait} {t t' : Nat}
    (h1 : Action.call n d w t ∈ (m.step s ev env).2)
    (h2 : Action.call n' d' w' t' ∈ (m.step (m.step s ev env).1 ev' env').2) : t' = t + 1 := by
  have e1 := (Machine.step_records_wait m s ev env h1).1
  have e2 := (Machine.step_records_wait m _ ev' env' h2).1
  rcases Machine.step_cases m (m.step s ev env).1 ev' env' with he | ⟨x, T, acts, _, hs, _, he⟩
  · rw [he] at h2; simp at h2
  · rw [e2, e1, he, Engine.realize_seq, hs]

/-- G6b: `seq` never decreases in a step, and grows by one whenever the step has actions. -/
theorem step_seq (m : Machine) (s : Snapshot) (ev : Event) (env : HostEnv) :
    (m.step s ev env).1.seq = s.seq ∧ (m.step s ev env).2 = [] ∨
      (m.step s ev env).1.seq = s.seq + 1 := by
  rcases Machine.step_cases m s ev env with he | ⟨x, T, acts, _, hs, _, he⟩
  · left; rw [he]; cases ev <;> simp [Snapshot.settle]
  · right; rw [he, Engine.realize_seq, hs]

/-! ## G7: at most one call per action with outcomes in a step (given V6 on the chart) -/

/-- The names of the calls that record a wait. -/
def waitNames (l : List Action) : List String :=
  l.filterMap fun
    | .call n _ w _ => if w = .free then none else some n
    | _ => none

theorem waitNames_append (a b : List Action) : waitNames (a ++ b) = waitNames a ++ waitNames b := by
  simp [waitNames, List.filterMap_append]

theorem waitNames_realizeOne (tok : Nat) (x : Snapshot) (out : List Action) (a : Action) :
    waitNames (realizeOne tok (x, out) a).2 = waitNames out ++ waitNames [a] := by
  cases a with
  | call n d w t => simp only [realizeOne, waitNames_append]; cases w <;> rfl
  | _ => simp [realizeOne, waitNames, List.filterMap_append]

theorem waitNames_foldl (tok : Nat) (acts : List Action) (x : Snapshot) (out : List Action) :
    waitNames (acts.foldl (realizeOne tok) (x, out)).2 = waitNames out ++ waitNames acts := by
  induction acts generalizing x out with
  | nil => simp [waitNames]
  | cons a as ih =>
    simp only [List.foldl_cons]
    have hr : realizeOne tok (x, out) a = ((realizeOne tok (x, out) a).1, (realizeOne tok (x, out) a).2) := rfl
    rw [hr, ih, waitNames_realizeOne]
    have : waitNames (a :: as) = waitNames [a] ++ waitNames as := by
      rw [show a :: as = [a] ++ as from rfl, waitNames_append]
    rw [this, List.append_assoc]

theorem waitNames_realize (s : Snapshot) (T : NodeId) (acts : List Action) :
    waitNames (realize s T acts).2 = waitNames acts := by
  unfold realize
  rw [waitNames_foldl]
  simp [waitNames]

theorem waitNames_entryActs (c : Chart) (T : NodeId) : waitNames (c.entryActs T) = [] := by
  simp only [Chart.entryActs, waitNames_append]
  refine List.append_eq_nil_iff.mpr ⟨List.append_eq_nil_iff.mpr ⟨?_, ?_⟩, by simp [waitNames]⟩
  · simp only [waitNames, List.filterMap_eq_nil_iff]
    intro a ha
    simp only [List.mem_filterMap] at ha
    obtain ⟨x, _, hx⟩ := ha
    split at hx <;> simp at hx
    subst hx; rfl
  · simp [waitNames]

theorem waitNames_cancels (l : List Action) (h : ∀ a ∈ l, ∃ id, a = .cancelTimer id) :
    waitNames l = [] := by
  simp only [waitNames, List.filterMap_eq_nil_iff]
  intro a ha
  obtain ⟨id, rfl⟩ := h a ha
  rfl

theorem exitActs_cancels (c : Chart) (L : NodeId) : ∀ a ∈ c.exitActs L, ∃ id, a = .cancelTimer id := by
  intro a ha
  simp only [Chart.exitActs, List.mem_map] at ha
  obtain ⟨t, _, rfl⟩ := ha
  exact ⟨_, rfl⟩

theorem staleCancels_cancels (exit : List Action) (s : Snapshot) :
    ∀ a ∈ staleCancels exit s, ∃ id, a = .cancelTimer id := by
  intro a ha
  simp only [staleCancels, List.mem_map] at ha
  obtain ⟨p, _, rfl⟩ := ha
  exact ⟨_, rfl⟩

theorem timerCancels_cancels (s : Snapshot) :
    ∀ a ∈ s.timers.map (fun p => Action.cancelTimer p.1), ∃ id, a = .cancelTimer id := by
  intro a ha
  simp only [List.mem_map] at ha
  obtain ⟨p, _, rfl⟩ := ha
  exact ⟨_, rfl⟩

/-- V6 for a chart: in every variant an action with outcomes occurs at most once. -/
def V6 (c : Chart) : Prop := ∀ t ∈ c.transitions, ∀ b ∈ t.branches, (waitNames b.actions).Nodup

theorem cands_mem (c : Chart) {L : NodeId} {e : EventId} {b : Branch} (h : b ∈ c.cands L e) :
    ∃ t ∈ c.transitions, b ∈ t.branches := by
  simp only [Chart.cands, Chart.branchesAt, List.mem_flatMap, List.mem_filter] at h
  obtain ⟨_, _, t, ⟨ht, _⟩, hb⟩ := h
  exact ⟨t, ht, hb⟩

theorem choose_mem (c : Chart) {L : NodeId} {ev : Event} {env : HostEnv} {b : Branch}
    (h : c.choose L ev env = some b) : ∃ t ∈ c.transitions, b ∈ t.branches := by
  unfold Chart.choose at h
  split at h
  · cases h
  · exact cands_mem c (List.mem_of_find?_eq_some h)

theorem enterBot_waitNames (c : Chart) (hv : V6 c) (s : Snapshot) (pre : List Action) (ev : Event)
    (env : HostEnv) (hpre : ∀ a ∈ pre, ∃ id, a = .cancelTimer id) :
    (waitNames (c.enterBot s pre ev env).2).Nodup := by
  unfold Chart.enterBot
  split
  · rename_i b hb
    rw [waitNames_realize, waitNames_append, waitNames_append, waitNames_cancels pre hpre,
      waitNames_entryActs]
    simp only [List.nil_append, List.append_nil]
    have hb' : c.choose (c.enter c.root) ev env = some b := by
      split at hb
      · exact hb
      · cases hb
    obtain ⟨t, ht, hbt⟩ := choose_mem c hb'
    exact hv t ht b hbt
  · rw [waitNames_realize, waitNames_append, waitNames_cancels pre hpre, Chart.initActs,
      waitNames_entryActs]
    exact List.nodup_nil

/-- **G7. Given V6, a step of the reference semantics calls each action with outcomes at most
once** (so it records at most one wait per action and its calls carry distinct
(action, token) pairs). -/
theorem chart_step_waitNames (c : Chart) (hv : V6 c) (s : Snapshot) (ev : Event) (env : HostEnv) :
    (waitNames (c.step s ev env).2).Nodup := by
  unfold Chart.step
  split
  · exact enterBot_waitNames c hv s _ ev env (timerCancels_cancels s)
  · split
    · exact List.nodup_nil
    · split
      · exact List.nodup_nil
      · rename_i b hb
        obtain ⟨t, ht, hbt⟩ := cands_mem c (List.mem_of_find?_eq_some hb)
        split
        · simp only [stay]; rw [waitNames_realize]; exact hv t ht b hbt
        · simp only [Chart.fire]
          rw [waitNames_realize, waitNames_append, waitNames_append, waitNames_append,
            waitNames_cancels _ (exitActs_cancels c _), waitNames_cancels _ (staleCancels_cancels _ _),
            waitNames_entryActs]
          simpa using hv t ht b hbt

/-- G7 for the compiled machine (through `compile_correct`). -/
theorem compiled_step_waitNames (c : Chart) (hwf : c.compile.wf = true) (hv : V6 c) (s : Snapshot)
    (ev : Event) (env : HostEnv) : (waitNames (c.compile.step s ev env).2).Nodup := by
  rw [Chart.compile_correct c hwf]
  exact chart_step_waitNames c hv s ev env

/-- G7 on a concrete document: `pizza` and `features` satisfy V6. -/
example : V6 Figram.Examples.features := by
  intro t ht b hb
  have : ∀ t ∈ Figram.Examples.features.transitions, ∀ b ∈ t.branches,
      (waitNames b.actions).Nodup = ((waitNames b.actions).Nodup) := fun _ _ _ _ => rfl
  revert t ht b hb
  decide


theorem realizeOne_out_calls (tok : Nat) (x : Snapshot) (out : List Action) (a : Action)
    (ha : ∃ n d w t, a = Action.call n d w t) :
    (realizeOne tok (x, out) a).2 = out ++ [match a with | .call n d w _ => .call n d w tok | a => a] := by
  obtain ⟨n, d, w, t, rfl⟩ := ha; rfl

theorem foldl_out_calls (tok : Nat) (acts : List Action) (x : Snapshot) (out : List Action)
    (hacts : ∀ a ∈ acts, ∃ n d w t, a = Action.call n d w t)
    (hout : ∀ a ∈ out, ∃ n d w t, a = Action.call n d w t) :
    ∀ a ∈ (acts.foldl (realizeOne tok) (x, out)).2, ∃ n d w t, a = Action.call n d w t := by
  induction acts generalizing x out with
  | nil => exact hout
  | cons a as ih =>
    simp only [List.foldl_cons]
    have hr : realizeOne tok (x, out) a = ((realizeOne tok (x, out) a).1, (realizeOne tok (x, out) a).2) := rfl
    rw [hr]
    apply ih _ _ (fun b hb => hacts b (List.mem_cons_of_mem _ hb))
    rw [realizeOne_out_calls tok x out a (hacts a List.mem_cons_self)]
    intro b hb
    rcases List.mem_append.mp hb with hb | hb
    · exact hout b hb
    · obtain ⟨n, d, w, t, rfl⟩ := hacts a List.mem_cons_self
      simp at hb; subst hb; exact ⟨_, _, _, _, rfl⟩

theorem realize_out_calls (s : Snapshot) (T : NodeId) (acts : List Action)
    (hacts : ∀ a ∈ acts, ∃ n d w t, a = Action.call n d w t) :
    ∀ a ∈ (acts.foldl (realizeOne (s.seq + 1)) ({ s with state := T, seq := s.seq + 1 }, [])).2,
      ∃ n d w t, a = Action.call n d w t :=
  foldl_out_calls _ _ _ _ hacts (by intro a ha; simp at ha)

/-! ## G8: the actions of a document's variants are calls (the hypothesis of `stay_keeps`) -/

def AllCalls (ts : List Transition) : Prop :=
  ∀ t ∈ ts, ∀ b ∈ t.branches, ∀ a ∈ b.actions, ∃ n d w tok, a = Action.call n d w tok

theorem onTrans_calls (o : CallOpts) (id : NodeId) (on : List (EventId × List Variant)) :
    AllCalls (onTrans o id on) := by
  intro t ht b hb a ha
  simp only [onTrans, List.mem_map] at ht
  obtain ⟨p, _, rfl⟩ := ht
  simp only [List.mem_map] at hb
  obtain ⟨v, _, rfl⟩ := hb
  simp only [Variant.toBranch, List.mem_map] at ha
  obtain ⟨n, _, rfl⟩ := ha
  exact ⟨_, _, _, _, rfl⟩

theorem allCalls_append {a b : List Transition} (ha : AllCalls a) (hb : AllCalls b) :
    AllCalls (a ++ b) := by
  intro t ht
  rcases List.mem_append.mp ht with h | h
  · exact ha t h
  · exact hb t h

mutual
theorem flatNode_calls (o : CallOpts) (id : NodeId) (par : Option NodeId) :
    (n : NodeDoc) → AllCalls (flatNode o id par n).2.1
  | .message on ts ext => by
    simp only [flatNode]; exact onTrans_calls o id on
  | .sec i cs on ext => by
    simp only [flatNode, Flat.app]
    exact allCalls_append (onTrans_calls o id on) (flatChildren_calls o id cs)
theorem flatChildren_calls (o : CallOpts) (par : NodeId) :
    (cs : List (NodeId × NodeDoc)) → AllCalls (flatChildren o par cs).2.1
  | [] => by simp only [flatChildren]; intro t ht; simp at ht
  | (k, n) :: rest => by
    simp only [flatChildren, Flat.app]
    exact allCalls_append (flatNode_calls o k (some par) n) (flatChildren_calls o par rest)
end

/-- **G8.** Every action of every variant of a document is a call of the developer's code. -/
theorem toChart_calls (d : Doc) : AllCalls d.toChart.transitions :=
  flatNode_calls _ _ _ d.bot

/-- **G8b. Decision 13 for every document**: a step of the compiled machine of a document that
takes a variant without a target keeps the message, the remembered places and the place to go
back to, consumes only the timer that fired, and emits only calls (no cancel_timer, start_timer
or send). -/
theorem doc_stay (d : Doc) (hwf : d.toChart.compile.wf = true) (s : Snapshot) (ev : Event)
    (env : HostEnv) (hs : s.state ∈ d.toChart.leafIds) (ha : s.accepts ev = true)
    (ho : ownsTimer (d.toChart.exitActs s.state) ev = true) (b : Branch)
    (hb : (d.toChart.cands s.state ev.key).find? (fun b => guardOK env b.guard) = some b)
    (hst : b.target = .stay) :
    let r := d.toChart.compile.step s ev env
    r.1.state = s.state ∧ r.1.resume = s.resume ∧ r.1.back = s.back ∧
      r.1.timers = (s.consume ev).timers ∧ r.1.seq = s.seq + 1 ∧
      ∀ a ∈ r.2, ∃ n dt w tok, a = Action.call n dt w tok := by
  intro r
  have hstep : r = stay (s.settle ev) ev b.actions := by
    show d.toChart.compile.step s ev env = _
    rw [Chart.compile_correct _ hwf]
    have hs' : ¬ s.state ∉ d.toChart.leafIds := fun h => h hs
    simp only [Chart.step, hs', ↓reduceIte, ha, ho, Bool.and_self, Bool.not_true,
      Bool.false_eq_true, hb, hst]
  obtain ⟨t, ht, hbt⟩ := cands_mem _ (List.mem_of_find?_eq_some hb)
  have hcalls : ∀ a ∈ b.actions, ∃ n dt w tok, a = Action.call n dt w tok :=
    toChart_calls d t ht b hbt
  have hk := Machine.stay_keeps (s.settle ev) ev b.actions
    (fun a h => Or.inl (hcalls a h))
  have hc : (s.settle ev).consume ev = { s.consume ev with pending := (s.settle ev).pending } := by
    cases ev <;> rfl
  refine ⟨?_, ?_, ?_, ?_, ?_, ?_⟩
  · rw [hstep]; rw [hk.1]; cases ev <;> rfl
  · rw [hstep]; rw [hk.2.1]; cases ev <;> rfl
  · rw [hstep]; rw [hk.2.2.1]; cases ev <;> rfl
  · rw [hstep]; rw [hk.2.2.2.1, hc]
  · rw [hstep]; rw [hk.2.2.2.2]; cases ev <;> rfl
  · rw [hstep]
    intro a ha
    simp only [stay, realize] at ha
    have hout := waitNames_foldl -- (not needed; see below)
    clear hout
    exact realize_out_calls _ _ _ hcalls a ha


/-! ## G9: for a compiled document, `restart` leaves no wait at all -/

def NoCall (a : Action) : Prop := ∀ n d w t, a ≠ Action.call n d w t

theorem foldl_pending_nocall (tok : Nat) (acts : List Action) (acc : Snapshot × List Action)
    (h : ∀ a ∈ acts, NoCall a) : (acts.foldl (realizeOne tok) acc).1.pending = acc.1.pending := by
  induction acts generalizing acc with
  | nil => rfl
  | cons a as ih =>
    simp only [List.foldl_cons]
    rw [ih _ (fun b hb => h b (List.mem_cons_of_mem _ hb))]
    have ha := h a List.mem_cons_self
    cases a with
    | call n d w t => exact absurd rfl (ha n d w t)
    | _ => rfl

theorem entryActs_nocall (c : Chart) (T : NodeId) : ∀ a ∈ c.entryActs T, NoCall a := by
  intro a ha n d w t he
  subst he
  simp only [Chart.entryActs, List.mem_append, List.mem_filterMap, List.mem_map, List.mem_singleton] at ha
  rcases ha with (⟨x, _, hx⟩ | ⟨x, _, hx⟩) | hx
  · split at hx <;> simp at hx
  · cases hx
  · cases hx

/-- **G9.** For the compiled machine of any chart, `restart` (as the engine runs it,
`handle_restart`) leaves the snapshot without waits: every late outcome of an earlier call is
then ignored (`step_rejected`). -/
theorem compiled_restart_no_waits (c : Chart) (s : Snapshot) :
    (c.compile.recover { s with resume := [], back := none, pending := [] }).1.pending = [] := by
  rw [Machine.recover_eq_realize, Chart.compile_eq_spec]
  unfold realize
  rw [foldl_pending_nocall]
  intro a ha
  rcases List.mem_append.mp ha with ha | ha
  · obtain ⟨id, rfl⟩ := timerCancels_cancels _ a ha
    intro n d w t h; cases h
  · exact entryActs_nocall c _ a ha

/-! ## G10: each call is answered once, over a whole session -/

theorem step_absent (m : Machine) (s : Snapshot) (ev : Event) (env : HostEnv) {c : String} {tok : Nat}
    (hp : (c, tok) ∉ s.pending) (ht : tok ≤ s.seq) :
    (c, tok) ∉ (m.step s ev env).1.pending ∧ tok ≤ (m.step s ev env).1.seq := by
  refine ⟨fun h => ?_, ?_⟩
  · rcases Machine.step_pending_new m s ev env h with h | h
    · exact hp (settle_pending_mem h)
    · simp at h; omega
  · rcases step_seq m s ev env with ⟨h, _⟩ | h <;> omega

theorem run_absent (m : Machine) (s : Snapshot) (inp : List Input) {c : String} {tok : Nat}
    (hp : (c, tok) ∉ s.pending) (ht : tok ≤ s.seq) :
    (c, tok) ∉ (m.run s inp).1.pending ∧ tok ≤ (m.run s inp).1.seq := by
  induction inp generalizing s with
  | nil => exact ⟨hp, ht⟩
  | cons x rest ih =>
    obtain ⟨e, env⟩ := x
    obtain ⟨h1, h2⟩ := step_absent m s e env hp ht
    exact ih _ h1 h2

/-- **G10. Each call is answered once, for good**: after an outcome of a call (with a token
given before the snapshot, as every accepted one in an engine-written snapshot), whatever events
follow, any later outcome of the same call is ignored. `step_outcome_once` is the case of no
event in between. -/
theorem outcome_once_forever (m : Machine) (hwf : m.wf = true) (s : Snapshot) (c o o' : String)
    (tok : Nat) (env env' : HostEnv) (inp : List Input) (ht : tok ≤ s.seq) :
    let s' := (m.run (m.step s (.outcome c o tok) env).1 inp).1
    m.step s' (.outcome c o' tok) env' = (s', []) := by
  intro s'
  have h0 : (c, tok) ∉ (m.step s (.outcome c o tok) env).1.pending := by
    intro h
    rcases Machine.step_pending_new m s _ env h with h | h
    · simp [Snapshot.settle] at h
    · simp at h; omega
  have h0' : tok ≤ (m.step s (.outcome c o tok) env).1.seq := by
    rcases step_seq m s (.outcome c o tok) env with ⟨h, _⟩ | h <;> omega
  obtain ⟨h1, _⟩ := run_absent m _ inp h0 h0'
  have hst : s'.state ∈ m.states := by
    cases inp with
    | nil => exact Machine.step_state_valid m hwf s _ env
    | cons x rest => exact Machine.run_state_valid m hwf _ x rest
  apply Machine.step_rejected _ _ _ _ hst
  simp only [Snapshot.accepts, decide_eq_false_iff_not]
  exact h1

end Audit5.Gaps

#print axioms Audit5.Gaps.step_nodup
#print axioms Audit5.Gaps.startWith_nodup
#print axioms Audit5.Gaps.restart_nodup
#print axioms Audit5.Gaps.run_nodup
#print axioms Audit5.Gaps.outcome_accepted_any_version
#print axioms Audit5.Gaps.outcome_no_arrow_any_version
#print axioms Audit5.Gaps.stay_timer_redelivery
#print axioms Audit5.Gaps.no_arrow_timer_redelivery_accepted
#print axioms Audit5.Gaps.chart_outcome_unmatched
#print axioms Audit5.Gaps.decEvent_named_key
#print axioms Audit5.Gaps.consecutive_tokens
#print axioms Audit5.Gaps.step_seq
#print axioms Audit5.Gaps.chart_step_waitNames
#print axioms Audit5.Gaps.compiled_step_waitNames
#print axioms Audit5.Gaps.toChart_calls
#print axioms Audit5.Gaps.doc_stay
#print axioms Audit5.Gaps.compiled_restart_no_waits
#print axioms Audit5.Gaps.outcome_once_forever

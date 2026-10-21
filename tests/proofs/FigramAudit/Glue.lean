/-
Glue lemmas. They connect the product theorems end to end, closing gaps between
what the theorems state and what docs/proofs.md claims. Build: `lake build FigramAudit` (lean_lib with
srcDir = "tests/proofs") or `lake env lean tests/proofs/FigramAudit/Glue.lean`.
-/
import Figram
open Figram Figram.Format Figram.Engine

namespace Figram.Chart

/-- G1: `lint` is exactly `lintCore` plus the final `wf` check of the compiled
machine — computed without building the machine (`wfDirect`, equal to `compile.wf` by
`wfDirect_eq`), so `check` no longer compiles the document. -/
theorem lint_eq (c : Chart) : c.lint =
    (if (c.lintCore.isEmpty && !c.compile.wf) = true then c.lintCore ++ [lintMsg] else c.lintCore) := by
  unfold lint
  rw [wfDirect_eq]

-- `lint_nil_wf : c.lint = [] → c.compile.wf = true` is now a product theorem (Figram/Lint.lean).
end Figram.Chart

namespace Figram.Audit
open Figram.Chart (lint_nil_wf)

/-! ## G1/G2: `check = []` (more precisely: `lint = []`) implies `compile.wf` -/

theorem check_lint_nil (d : Doc) (h : d.check = []) : d.toChart.lint = [] := by
  obtain ⟨X, hX⟩ : ∃ X, d.check = X ++ d.toChart.lint := ⟨_, rfl⟩
  rw [hX] at h
  exact (List.append_eq_nil_iff.mp h).2

theorem check_compile_wf (d : Doc) (h : d.check = []) : d.toChart.compile.wf = true :=
  lint_nil_wf _ (check_lint_nil d h)

/-- G3: a document that passed `check` is compiled correctly (no separate `wf` hypothesis). -/
theorem check_compile_correct (d : Doc) (h : d.check = []) (s : Snapshot) (ev : Event)
    (env : HostEnv) : d.toChart.compile.step s ev env = d.toChart.step s ev env :=
  d.toChart.compile_correct (check_compile_wf d h) s ev env

/-! ## G4: every machine accepted by `Engine.load` satisfies the theorem hypotheses -/

theorem load_sound {txt : String} {m : Machine} (h : Engine.load txt = .ok m) :
    ∃ d : Doc, d.check = [] ∧ m = d.toChart.compile ∧ m.wf = true ∧ namesOK m = true := by
  unfold Engine.load at h
  cases hp : (parse txt >>= decode) with
  | error e => rw [hp] at h; cases h
  | ok d =>
    rw [hp] at h
    simp only at h
    by_cases hc : d.check.isEmpty = true
    · simp only [hc, Bool.not_true, Bool.false_eq_true, ite_false] at h
      by_cases hw : (d.toChart.compile.wfFast && namesOK d.toChart.compile) = true
      · simp only [hw, ite_true, Except.ok.injEq] at h
        simp only [Bool.and_eq_true, Machine.wfFast_eq] at hw
        exact ⟨d, List.isEmpty_iff.mp hc, h.symm, h ▸ hw.1, h ▸ hw.2⟩
      · simp only [hw] at h
        cases h
    · simp only [hc] at h
      cases h


/-! ## G5/G6: decision 7 at the protocol level (it does NOT hold in the Core model) -/

theorem decEvent_named {kvs : Obj} {n : String} (h : decEvent kvs = .ok (.named n)) :
    n.startsWith "timer:" = false := by
  unfold decEvent at h
  cases hg : get kvs "event" with
  | none => simp [hg, bind, Except.bind, throw, throwThe, MonadExceptOf.throw] at h
  | some j =>
    cases j with
    | str e =>
      -- `decEvent` also rejects an empty event, an empty timer name and a token on a
      -- named event; the statement is unchanged
      by_cases he : e.isEmpty = true
      · simp [hg, he, bind, Except.bind, throw, throwThe, MonadExceptOf.throw] at h
      · simp only [hg, he, Bool.false_eq_true, ite_false, bind, Except.bind, pure, Except.pure] at h
        by_cases hs : e.startsWith "timer:" = true
        · simp only [hs, ite_true] at h
          split at h
          · simp [throw, throwThe, MonadExceptOf.throw] at h
          · split at h
            · split at h <;> simp at h
            · simp [throw, throwThe, MonadExceptOf.throw] at h
        · simp only [hs, Bool.false_eq_true, ite_false] at h
          split at h
          · simp [throw, throwThe, MonadExceptOf.throw] at h
          · simp only [Except.ok.injEq, Event.named.injEq] at h
            subst h
            simpa using hs
    | _ => simp [hg, bind, Except.bind, throw, throwThe, MonadExceptOf.throw] at h

/-- Any step that fires on an event whose key starts with `timer:` and that was decoded by
`decEvent` is a timer event carrying the CURRENT token of that timer. -/
theorem timer_key_needs_token (m : Machine) {kvs : Obj} {ev : Event}
    (hev : decEvent kvs = .ok ev) (hk : ev.key.startsWith "timer:" = true)
    (s : Snapshot) (env : HostEnv) (hs : s.state ∈ m.states)
    (hfire : m.step s ev env ≠ (s, [])) :
    ∃ id tok, ev = .timer id tok ∧ lookup s.timers id = some tok := by
  cases ev with
  | named n =>
    have := decEvent_named hev
    simp only [Event.key] at hk
    rw [this] at hk
    cases hk
  | timer id tok =>
    refine ⟨id, tok, rfl, ?_⟩
    by_cases hl : lookup s.timers id = some tok
    · exact hl
    · exfalso
      apply hfire
      unfold Machine.step
      simp [hs, Snapshot.accepts, hl]

/-! ## G7: end to end — a `step` request to a loaded engine computes the reference
semantics `Chart.step` of the loaded document (model level: `Obj`, not text). -/

theorem engine_step_is_reference {txt : String} {m : Machine} (hload : Engine.load txt = .ok m)
    (kvs : Obj) (j : J) (s : Snapshot) (ev : Event) (gs : List (String × Bool))
    (hop : get kvs "op" = some (.str "step")) (hsn : get kvs "snapshot" = some j)
    (hj : decSnapshot j = .ok s) (hev : decEvent kvs = .ok ev) (hgs : decGuards kvs = .ok gs)
    (hmiss : missing m s ev gs = []) (hseq : (m.step s ev (envOf gs)).1.seq ≤ maxInt) :
    ∃ d : Doc, d.check = [] ∧ m = d.toChart.compile ∧
      handleObj m kvs = .ok (result ((get kvs "id").getD .null)
        (d.toChart.step s ev (envOf gs)).1 (d.toChart.step s ev (envOf gs)).2
        (decide (s.state ∉ d.toChart.leafIds))) := by
  obtain ⟨d, hc, hm, hwf, _⟩ := load_sound hload
  refine ⟨d, hc, hm, ?_⟩
  rw [handle_step m kvs j s ev gs hop hsn hj hev hgs hmiss hseq]
  subst hm
  rw [Chart.compile_correct _ hwf]
  rfl

/-! ## G8: `start` and `restart` also emit admissible snapshots (the engine never produces a
snapshot it could not read back; the product theorems state it for `step` and `startAt`). -/

theorem start_snapOK (m : Machine) (hwf : m.wf = true) (hn : namesOK m = true) :
    SnapOK m.start.1 := by
  simp only [namesOK, Bool.and_eq_true, List.all_eq_true] at hn
  obtain ⟨⟨⟨⟨hstates, hinit⟩, _⟩, _⟩, _⟩ := hn
  have hmem := m.start_state_valid hwf
  have hinv := foldl_inv 1 (by decide) m.initActions hinit
    ({ Snapshot.fresh with state := m.initial, seq := Snapshot.fresh.seq + 1 }, [])
    ⟨fun p hp => by simp [Snapshot.fresh] at hp, fun p hp => by simp [Snapshot.fresh] at hp⟩
  refine ⟨?_, hinv.1, hinv.2, ?_⟩
  · exact ne_of_not_isEmpty (hstates _ hmem)
  · show (realize Snapshot.fresh m.initial m.initActions).1.seq ≤ maxInt
    rw [realize_seq]; decide

theorem restart_snapOK (m : Machine) (hwf : m.wf = true) (hn : namesOK m = true)
    (s : Snapshot) (hs : SnapOK s) (hseq : s.seq + 1 ≤ maxInt) :
    SnapOK (m.recover { s with resume := [] }).1 := by
  simp only [namesOK, Bool.and_eq_true, List.all_eq_true] at hn
  obtain ⟨⟨⟨⟨hstates, hinit⟩, _⟩, _⟩, _⟩ := hn
  have hi := (Machine.wf_parts hwf).1
  unfold Machine.recover
  have hs' : SnapOK { s with resume := [] } := ⟨hs.state, by simp, hs.timers, hs.seq⟩
  refine realize_snapOK { s with resume := [] } m.initial _ hs'
    (ne_of_not_isEmpty (hstates _ hi)) ?_ hseq
  · intro a ha
    simp only [List.mem_append, List.mem_map] at ha
    rcases ha with ⟨_, _, rfl⟩ | ha
    · rfl
    · exact hinit a ha

/-! ## G9: the snapshot decoder only produces admissible snapshots (`SnapOK`), so the
input hypothesis of `step_snapOK`/`reply_readable` holds for every decoded request. -/

theorem except_throw_ne {α : Type} {e : String} {a : α} : (throw e : Except String α) ≠ .ok a := by
  simp [throw, throwThe, MonadExceptOf.throw]
theorem nonEmpty_ok {w : String} {j : J} {s : String} (h : nonEmpty w j = .ok s) : s ≠ "" := by
  cases j with
  | str t =>
    simp only [nonEmpty] at h
    split at h
    · exact absurd h except_throw_ne
    · rename_i ht
      simp only [pure, Except.pure, Except.ok.injEq] at h
      subst h
      intro he; subst he; simp at ht
  | _ => exact absurd h except_throw_ne
theorem except_bind_ok {ε α β : Type} {x : Except ε α} {f : α → Except ε β} {b : β} :
    (x >>= f) = .ok b ↔ ∃ a, x = .ok a ∧ f a = .ok b := by
  cases x <;> simp [bind, Except.bind]
theorem except_pure_ok {ε α : Type} {a b : α} : (pure a : Except ε α) = .ok b ↔ a = b := by
  simp [pure, Except.pure]
theorem boundedNat_ok {w : String} {j : J} {n : Nat} (h : boundedNat w j = .ok n) : n ≤ maxInt := by
  cases j with
  | num m e =>
    simp only [boundedNat] at h
    split at h
    · split at h
      · rename_i hle
        simp only [pure, Except.pure, Except.ok.injEq] at h
        subst h; exact hle
      · exact absurd h except_throw_ne
    · exact absurd h except_throw_ne
  | _ => exact absurd h except_throw_ne

theorem mapM_except_mem {α β ε : Type} (f : α → Except ε β) :
    ∀ (l : List α) (r : List β), l.mapM f = .ok r → ∀ y ∈ r, ∃ x ∈ l, f x = .ok y
  | [], r, h => by
    simp only [List.mapM_nil, pure, Except.pure, Except.ok.injEq] at h
    subst h; simp
  | x :: xs, r, h => by
    rw [List.mapM_cons, except_bind_ok] at h
    obtain ⟨y, hy, h⟩ := h
    rw [except_bind_ok] at h
    obtain ⟨ys, hys, h⟩ := h
    rw [except_pure_ok] at h
    subst h
    intro z hz
    rcases List.mem_cons.mp hz with rfl | hz
    · exact ⟨x, List.mem_cons_self .., hy⟩
    · obtain ⟨w, hw, hfw⟩ := mapM_except_mem f xs ys hys z hz
      exact ⟨w, List.mem_cons_of_mem _ hw, hfw⟩

theorem decResumeEntry_ok {p : String × J} {q : String × String} (h : decResumeEntry p = .ok q) :
    q.1 ≠ "" ∧ q.2 ≠ "" := by
  obtain ⟨k, v⟩ := p
  cases hk : k.isEmpty
  · simp only [decResumeEntry, hk, Bool.false_eq_true, ite_false, except_bind_ok, except_pure_ok] at h
    obtain ⟨v', hv, rfl⟩ := h
    refine ⟨?_, nonEmpty_ok hv⟩
    intro he; subst he; simp at hk
  · simp only [decResumeEntry, hk, ite_true, except_bind_ok] at h
    obtain ⟨_, h, _⟩ := h
    exact absurd h except_throw_ne

theorem decTimerEntry_ok {p : String × J} {q : String × Nat} (h : decTimerEntry p = .ok q) :
    q.1 ≠ "" ∧ q.2 ≤ maxInt := by
  obtain ⟨k, v⟩ := p
  cases hk : k.isEmpty
  · simp only [decTimerEntry, hk, Bool.false_eq_true, ite_false, except_bind_ok, except_pure_ok] at h
    obtain ⟨v', hv, rfl⟩ := h
    refine ⟨?_, boundedNat_ok hv⟩
    intro he; subst he; simp at hk
  · simp only [decTimerEntry, hk, ite_true, except_bind_ok] at h
    obtain ⟨_, h, _⟩ := h
    exact absurd h except_throw_ne

theorem decSnapshot_sound {j : J} {s : Snapshot} (h : decSnapshot j = .ok s) : SnapOK s := by
  cases j with
  | obj kvs =>
    simp only [decSnapshot] at h
    rw [except_bind_ok] at h; obtain ⟨_, _, h⟩ := h
    rw [except_bind_ok] at h; obtain ⟨_, _, h⟩ := h
    rcases hat : Format.get kvs "at" with _ | ja
    · simp only [hat] at h; exact absurd h except_throw_ne
    simp only [hat] at h
    rw [except_bind_ok] at h; obtain ⟨st, hst, h⟩ := h
    have hst := nonEmpty_ok hst
    rcases hr : Format.get kvs "resume" with _ | (_ | _ | _ | _ | _ | mr) <;>
      simp only [hr] at h <;> rw [except_bind_ok] at h <;> obtain ⟨res, hres, h⟩ := h <;>
      (try exact absurd hres except_throw_ne)
    all_goals (have hR : ∀ p ∈ res, p.1 ≠ "" ∧ p.2 ≠ "" := by
                 first
                 | (rw [except_pure_ok] at hres; subst hres; simp)
                 | (intro p hp; obtain ⟨x, _, hx⟩ := mapM_except_mem _ _ _ hres p hp
                    exact decResumeEntry_ok hx))
    all_goals (rcases ht : Format.get kvs "timers" with _ | (_ | _ | _ | _ | _ | mt) <;>
      simp only [ht] at h <;> rw [except_bind_ok] at h <;> obtain ⟨tms, htms, h⟩ := h <;>
      (try exact absurd htms except_throw_ne))
    all_goals (have hT : ∀ p ∈ tms, p.1 ≠ "" ∧ p.2 ≤ maxInt := by
                 first
                 | (rw [except_pure_ok] at htms; subst htms; simp)
                 | (intro p hp; obtain ⟨x, _, hx⟩ := mapM_except_mem _ _ _ htms p hp
                    exact decTimerEntry_ok hx))
    all_goals (rcases hq : Format.get kvs "seq" with _ | jq <;>
      simp only [hq] at h <;> rw [except_bind_ok] at h <;> obtain ⟨seq, hseq, h⟩ := h)
    all_goals (have hS : seq ≤ maxInt := by
                 first
                 | (rw [except_pure_ok] at hseq; subst hseq; simp [maxInt])
                 | exact boundedNat_ok hseq)
    all_goals (rw [except_pure_ok] at h; subst h; exact ⟨hst, hR, hT, hS⟩)
  | _ => exact absurd h except_throw_ne

/-- G10: closed protocol for `step` without an external `SnapOK` hypothesis: the snapshot
in a successful `step` reply of a loaded engine is read back by the engine unchanged. -/
theorem step_reply_readable {txt : String} {m : Machine} (hload : Engine.load txt = .ok m)
    {j : J} {s : Snapshot} (hj : decSnapshot j = .ok s) (ev : Event) (env : HostEnv)
    (hseq : (m.step s ev env).1.seq ≤ maxInt) :
    decSnapshot (encSnapshot (m.step s ev env).1) = .ok (m.step s ev env).1 := by
  obtain ⟨_, _, _, hwf, hn⟩ := load_sound hload
  exact reply_readable m hwf hn s ev env (decSnapshot_sound hj) hseq

end Figram.Audit

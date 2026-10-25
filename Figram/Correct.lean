import Figram.Compile
import Figram.CoreProofs

/-!
# The main theorem: the compiler preserves the behaviour

`compile_correct`: if the compiled machine passed the `wf` check, then for
**any** snapshot, any event and any values of the developer's conditions, a step
of the compiled machine is exactly the step of the chart under the reference
semantics: the same new snapshot and the same list of actions.

`start_correct`: the first entry agrees as well.
-/

namespace Figram

/-- A lookup in a table built from a list of keys. -/
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

namespace Machine

/-- The fast `wf` check (hash tables) equals the specification. -/
theorem wfFast_eq (m : Machine) : m.wfFast = m.wf := by
  have hst : ∀ n, (keySet m.states).contains n = decide (n ∈ m.states) := keySet_contains _
  have htgt : ∀ t : Target, targetIn (keySet m.states) (keySet (m.resumes.map (·.sec))) t =
      m.targetOK t := by
    intro t
    induction t with
    | node n => simp only [targetIn, targetOK, hst]
    | resume sec =>
      simp only [targetIn, targetOK, keySet_contains]
      apply Bool.eq_iff_iff.mpr
      simp only [decide_eq_true_eq, List.mem_map, List.any_eq_true]
    | back fb ih => simpa only [targetIn, targetOK] using ih
  unfold wfFast wf rows
  simp only [List.all_flatMap, htgt, hst]

end Machine

namespace Chart

variable (c : Chart)

/-! ## The fast compiler equals the specification

Every index of `Chart.index` answers like the list definition, so
`compile` builds exactly `compileSpec`. -/

theorem index_node (n : NodeId) : c.index.node.find? n = c.find n := by
  simp only [index, firstIndex_get, find, List.find?_map, Option.map_map]
  cases h : c.nodes.find? (fun x => decide (x.id = n)) <;> simp [h, Function.comp_def]

theorem index_parent (n : NodeId) : c.index.parent n = c.parent n := by
  simp [Index.parent, parent, index_node]

theorem index_ancAux (k : Nat) (n : NodeId) : c.index.ancAux k n = c.ancAux k n := by
  induction k generalizing n with
  | zero => rfl
  | succ k ih =>
    simp only [Index.ancAux, ancAux, index_parent]
    cases c.parent n with
    | none => rfl
    | some p => simp [ih]

theorem index_anc (n : NodeId) : c.index.anc n = c.anc n := by
  unfold Index.anc anc; rw [index_ancAux]; rfl

theorem index_enterAux (k : Nat) (n : NodeId) : c.index.enterAux k n = c.enterAux k n := by
  induction k generalizing n with
  | zero => rfl
  | succ k ih =>
    simp only [Index.enterAux, enterAux, index_node]
    split <;> simp_all

theorem index_enter (n : NodeId) : c.index.enter n = c.enter n := by
  unfold Index.enter enter; rw [index_enterAux]; rfl

theorem index_toTarget (d : Dest) : c.index.toTarget d = c.toTarget d := by
  induction d with
  | node n => simp only [Index.toTarget, toTarget, index_enter]
  | history _ => rfl
  | back fb ih => simp only [Index.toTarget, toTarget, ih]

theorem index_toF (b : Branch) : c.index.toF b = c.toF b := by
  simp only [Index.toF, toF, index_toTarget]

/-- Grouping singletons: key `f t`, value `[g t]`. -/
theorem singletons_get {α β} (l : List α) (f : α → String) (g : α → β) (k : String) :
    ((l.map fun t => (f t, [g t])).filter (fun p => decide (p.1 = k))).flatMap (·.2) =
      (l.filter fun t => decide (f t = k)).map g := by
  induction l with
  | nil => rfl
  | cons x xs ih =>
    simp only [List.map_cons, List.filter_cons]
    by_cases h : f x = k <;> simp [h, ih]

theorem index_timersOf (n : NodeId) : c.index.timersOf n = c.timersOf n := by
  simp only [Index.timersOf, index, concatIndex_get, timersOf]
  have := singletons_get c.timers (·.node) id n
  simpa using this

theorem index_hist (s : NodeId) : c.index.hist.contains s = decide (s ∈ c.histSecs) := by
  simp [index, keySet_contains]

theorem index_entryActs (T : NodeId) : c.index.entryActs T = c.entryActs T := by
  simp only [Index.entryActs, entryActs, index_anc, index_timersOf]
  congr 2
  congr 1
  funext s
  rw [index_hist]
  by_cases h1 : s ∈ c.histSecs <;> by_cases h2 : s = T <;> simp [h1, h2]

theorem index_exitActs (L : NodeId) : c.index.exitActs L = c.exitActs L := by
  simp only [Index.exitActs, exitActs, index_timersOf]


theorem transIndex_get (a : NodeId) :
    (c.transIndex c.index).concat a =
      (c.transitions.filter fun t => decide (t.src = a)).map fun t => (t.event, t.branches.map c.toF) := by
  simp only [transIndex, concatIndex_get, singletons_get]
  congr 1
  funext t
  simp [index_toF]

theorem pairs_event {α} (l : List Transition) (f : Branch → α) (e : EventId) :
    ((l.map fun t => (t.event, t.branches.map f)).filter (fun p => decide (p.1 = e))).flatMap (·.2) =
      (l.filter fun t => decide (t.event = e)).flatMap fun t => t.branches.map f := by
  induction l with
  | nil => rfl
  | cons x xs ih =>
    simp only [List.map_cons, List.filter_cons]
    by_cases h : x.event = e <;> simp [h, ih]

theorem leafTab_get (L e : NodeId) :
    (concatIndex ((c.index.anc L).flatMap fun a => (c.transIndex c.index).concat a)).concat e =
      (c.cands L e).map c.toF := by
  rw [concatIndex_get]
  simp only [transIndex_get, index_anc]
  rw [List.filter_flatMap, List.flatMap_assoc]
  simp only [cands, branchesAt, List.map_flatMap]
  congr 1
  funext a
  rw [pairs_event, List.filter_filter]
  congr 2
  funext t
  rw [Bool.and_comm]
  simp

theorem filterMap_rows (L : NodeId) (evs : List EventId) (f : EventId → List FBranch) :
    evs.filterMap (fun e => if (f e).isEmpty then none else some (⟨L, e, f e⟩ : Row)) =
      (evs.map fun e => (⟨L, e, f e⟩ : Row)).filter (fun r => !r.branches.isEmpty) := by
  induction evs with
  | nil => rfl
  | cons x xs ih =>
    simp only [List.filterMap_cons, List.map_cons, List.filter_cons]
    rw [ih]
    cases h : (f x).isEmpty <;> simp only [ite_true, ite_false, Bool.false_eq_true, Bool.not_true,
      Bool.not_false]

theorem leafRows_eq (L : NodeId) :
    leafRows ((dedupFast (c.transitions.map (·.event))).map fun e => (e, hash e))
      (c.transIndex c.index) c.index L = c.rowsSpec L := by
  unfold leafRows
  rw [List.filterMap_map]
  simp only [Function.comp_def, Tab.concatH_hash, leafTab_get]
  rw [filterMap_rows, dedupFast_eq]
  rfl

theorem filter_eq_of_nodup {xs : List String} (h : xs.Nodup) (s : String) :
    xs.filter (fun a => decide (a = s)) = if s ∈ xs then [s] else [] := by
  induction xs with
  | nil => simp
  | cons x xs ih =>
    have ⟨hx, hxs⟩ := List.nodup_cons.mp h
    simp only [List.filter_cons, ih hxs, List.mem_cons]
    by_cases hxs' : x = s
    · subst hxs'; simp [hx]
    · simp [hxs', Ne.symm hxs']

theorem flatMap_ite {α} (l : List α) (p : α → Bool) :
    l.flatMap (fun x => if p x then [x] else []) = l.filter p := by
  induction l with
  | nil => rfl
  | cons x xs ih => by_cases h : p x <;> simp [h, ih]

theorem memberIndex_get (s : NodeId) (hs : s ∈ c.histSecs) :
    (c.memberIndex c.index).concat s = c.histCands s := by
  rw [memberIndex, concatIndex_get, List.filter_flatMap, List.flatMap_assoc]
  have hl : ∀ l, (((dedupFast ((c.index.anc l).filter fun a => c.index.hist.contains a && a != l)).map
        fun a => (a, [l])).filter (fun p => decide (p.1 = s))).flatMap (·.2) =
      if decide (s ∈ c.anc l ∧ l ≠ s) then [l] else [] := by
    intro l
    have := singletons_get (dedupFast ((c.index.anc l).filter fun a => c.index.hist.contains a && a != l))
      id (fun _ => l) s
    simp only [id] at this
    rw [this, dedupFast_eq]
    simp only [filter_eq_of_nodup (nodup_dedup _), mem_dedup]
    simp only [List.mem_filter, index_anc, index_hist, hs]
    by_cases h1 : s ∈ c.anc l <;> by_cases h2 : s = l
    · subst h2; simp
    · have h2' : ¬ l = s := fun h => h2 h.symm
      simp [h1, h2, h2']
    · subst h2; simp
    · simp [h1]
  simp only [hl]
  rw [flatMap_ite]
  rfl

theorem compile_eq_spec : c.compile = c.compileSpec := by
  unfold compile compileSpec
  simp only [leafRows_eq]
  simp only [index_enter, index_entryActs, index_exitActs, index_parent, dedupFast_eq, initActs]
  congr 1
  apply List.map_congr_left
  intro s hs
  simp only [mkResume, memberIndex_get c s ((mem_dedup _ _).mp hs)]


/-! ## The arrows of the specification -/

theorem mem_cands {L : NodeId} {e : EventId} {b : Branch} (hb : b ∈ c.cands L e) :
    ∃ t ∈ c.transitions, t.event = e ∧ b ∈ t.branches := by
  simp only [cands, branchesAt, List.mem_flatMap, List.mem_filter, decide_eq_true_eq] at hb
  obtain ⟨_, _, t, ⟨ht, _, he⟩, hbt⟩ := hb
  exact ⟨t, ht, he, hbt⟩

theorem hist_mem {L : NodeId} {e : EventId} {b : Branch} {s : NodeId}
    (hb : b ∈ c.cands L e) (ht : b.target.histSec = some s) : s ∈ dedup c.histSecs := by
  obtain ⟨t, htr, _, hbt⟩ := c.mem_cands hb
  rw [mem_dedup]
  simp only [histSecs, List.mem_flatMap, List.mem_filterMap]
  exact ⟨t, htr, b, hbt, ht⟩

theorem event_mem {L : NodeId} {e : EventId} (h : c.cands L e ≠ []) : e ∈ c.events := by
  obtain ⟨b, hb⟩ := List.exists_mem_of_ne_nil _ h
  obtain ⟨t, htr, he, _⟩ := c.mem_cands hb
  simp only [events, mem_dedup, List.mem_map]
  exact ⟨t, htr, he⟩

/-- A lookup by event among the arrows of a message. -/
theorem find_rows (L e : NodeId) (evs : List EventId) :
    ((evs.map (c.compileRow L)).filter (fun r => !r.branches.isEmpty)).find?
        (fun r => decide (r.event = e)) =
      if e ∈ evs ∧ c.cands L e ≠ [] then some (c.compileRow L e) else none := by
  induction evs with
  | nil => simp
  | cons x xs ih =>
    simp only [List.map_cons, List.filter_cons]
    by_cases hx : x = e
    · subst hx
      by_cases hc : c.cands L x = []
      · have he : (c.compileRow L x).branches.isEmpty = true := by simp [compileRow, hc]
        simp only [he, Bool.not_true, Bool.false_eq_true, ite_false]
        rw [ih]
        simp [hc]
      · simp [compileRow, hc]
    · have hb : (decide ((c.compileRow L x).event = e)) = false := by simp [compileRow, hx]
      split
      · rw [List.find?_cons, hb, ih]
        by_cases he : e ∈ xs <;> simp [he, Ne.symm hx]
      · rw [ih]
        by_cases he : e ∈ xs <;> simp [he, Ne.symm hx]

/-- The arrow of the specification for a message and an event. -/
theorem spec_row {L : NodeId} (hL : L ∈ c.leafIds) (e : EventId) :
    c.compileSpec.row L e = if c.cands L e = [] then none else some (c.compileRow L e) := by
  unfold Machine.row
  simp only [compileSpec, lookup_map _ _ _ hL, Option.getD_some, rowsSpec, find_rows]
  by_cases h : c.cands L e = []
  · simp [h]
  · simp [h, c.event_mem h]

theorem row_mem {L : NodeId} {e : EventId} {b : Branch} (hL : L ∈ c.leafIds)
    (hb : b ∈ c.cands L e) : c.compileRow L e ∈ c.compileSpec.rows := by
  have hne : c.cands L e ≠ [] := List.ne_nil_of_mem hb
  simp only [Machine.rows, compileSpec, List.mem_flatMap, List.mem_map]
  refine ⟨(L, c.rowsSpec L), ⟨L, hL, rfl⟩, ?_⟩
  simp only [rowsSpec, List.mem_filter, List.mem_map]
  refine ⟨⟨e, c.event_mem hne, rfl⟩, ?_⟩
  simp [compileRow, hne]

/-- A target whose "resume" section has a `ResumeSpec` resolves as in the chart. -/
theorem resolve_toTarget (s : Snapshot) (d : Dest)
    (hd : ∀ s0, d.histSec = some s0 → s0 ∈ dedup c.histSecs) :
    c.compileSpec.resolve s (c.toTarget d) = c.resolve s d := by
  induction d with
  | node n => simp [toTarget, Machine.resolve, resolve]
  | history s0 =>
    have hm := hd s0 rfl
    have hf : c.compileSpec.resumes.find? (fun r => decide (r.sec = s0)) = some (c.mkResume s0) := by
      simp only [compileSpec, List.find?_map]
      have : ((fun r : ResumeSpec => decide (r.sec = s0)) ∘ c.mkResume) =
          fun x => decide (x = s0) := by funext x; rfl
      rw [this, find?_eq_self _ _ hm]
      rfl
    simp only [toTarget, Machine.resolve, hf, resolve, restoreTarget, mkResume]
    cases lookup s.resume s0 <;> rfl
  | back fb ih =>
    simp only [toTarget, Machine.resolve, resolve]
    rw [ih hd]
    rfl

theorem resolve_eq (s : Snapshot) {L : NodeId} {e : EventId} {b : Branch}
    (hb : b ∈ c.cands L e) : c.compileSpec.resolve s (c.toF b).target = c.resolve s b.target :=
  c.resolve_toTarget s b.target (fun _ h => c.hist_mem hb h)

/-- The section of a message in the compiled machine is its parent in the chart. -/
theorem spec_zoneOf {L : NodeId} (hL : L ∈ c.leafIds) : c.compileSpec.zoneOf L = c.parent L := by
  simp only [Machine.zoneOf, compileSpec, lookup_map _ _ _ hL, Option.getD_some]

/-- For the chosen variant the step of the compiled machine agrees with the chart. -/
theorem fire_eq (hwf : c.compileSpec.wf = true) (s : Snapshot) {e : EventId} {b : Branch}
    (hs : s.state ∈ c.leafIds) (hb : b ∈ c.cands s.state e) :
    c.compileSpec.fire s (c.toF b) = c.fire s b := by
  have hT := c.resolve_eq s hb
  have hrow := c.row_mem hs hb
  have hok : c.compileSpec.targetOK (c.toF b).target = true := by
    obtain ⟨_, hrows, _⟩ := Machine.wf_parts hwf
    exact hrows _ hrow _ (List.mem_map.mpr ⟨b, hb, rfl⟩)
  have hTmem : c.resolve s b.target ∈ c.leafIds := by
    rw [← hT]; exact Machine.resolve_mem hwf s _ hok
  have hex : c.compileSpec.exitOf s.state = c.exitActs s.state := by
    simp only [Machine.exitOf, compileSpec, lookup_map _ _ _ hs, Option.getD_some]
  have hen : c.compileSpec.entryOf (c.resolve s b.target) = c.entryActs (c.resolve s b.target) := by
    simp only [Machine.entryOf, compileSpec, lookup_map _ _ _ hTmem, Option.getD_some]
  have hmb : c.compileSpec.markBack s (c.resolve s b.target) = c.markBack s (c.resolve s b.target) := by
    simp only [Machine.markBack, markBack, c.spec_zoneOf hs, c.spec_zoneOf hTmem]
  rw [Machine.fire_eq_realize]
  simp only [fire, hT, hex, hen, hmb]
  rfl

/-- Entering the bot with an event: the compiled machine agrees with the chart. -/
theorem enterBot_eq (hwf : c.compileSpec.wf = true) (s : Snapshot) (pre : List Action)
    (ev : Event) (env : HostEnv) : c.compileSpec.enterBot s pre ev env = c.enterBot s pre ev env := by
  have hi : c.enter c.root ∈ c.leafIds := (Machine.wf_parts hwf).1
  rw [Machine.enterBot_eq_realize]
  unfold Chart.enterBot
  cases ev with
  | timer => rfl
  | named e =>
    have hch : c.compileSpec.choose c.compileSpec.initial (.named e) env =
        ((c.cands (c.enter c.root) e).find? (fun b : Branch => guardOK env b.guard)).map c.toF := by
      simp only [Machine.choose]
      show (match c.compileSpec.row (c.enter c.root) e with
        | none => none
        | some r => r.branches.find? (fun b : FBranch => guardOK env b.guard)) = _
      rw [c.spec_row hi]
      by_cases hc : c.cands (c.enter c.root) e = []
      · simp [hc]
      · simp only [hc, ite_false, compileRow, List.find?_map]
        rfl
    rw [hch]
    simp only [Chart.choose]
    cases hb : (c.cands (c.enter c.root) e).find? (fun b => guardOK env b.guard) with
    | none => rfl
    | some b =>
      have hbm := List.mem_of_find?_eq_some hb
      have hT := c.resolve_eq s hbm
      have hok : c.compileSpec.targetOK (c.toF b).target = true := by
        obtain ⟨_, hrows, _⟩ := Machine.wf_parts hwf
        exact hrows _ (c.row_mem hi hbm) _ (List.mem_map.mpr ⟨b, hbm, rfl⟩)
      have hTmem : c.resolve s b.target ∈ c.leafIds := by
        rw [← hT]; exact Machine.resolve_mem hwf s _ hok
      have hen : c.compileSpec.entryOf (c.resolve s b.target) = c.entryActs (c.resolve s b.target) := by
        simp only [Machine.entryOf, compileSpec, lookup_map _ _ _ hTmem, Option.getD_some]
      simp only [Option.map_some, hT, hen]
      rfl

theorem compileSpec_correct (hwf : c.compileSpec.wf = true) (s : Snapshot) (ev : Event)
    (env : HostEnv) : c.compileSpec.step s ev env = c.step s ev env := by
  unfold Machine.step Chart.step
  by_cases hs : s.state ∈ c.leafIds
  · have hs' : s.state ∈ c.compileSpec.states := hs
    -- The acceptance condition of an event reads the exit actions (`ownsTimer`):
    -- they are the same for the compiled machine and the chart.
    have hex : c.compileSpec.exitOf s.state = c.exitActs s.state := by
      simp only [Machine.exitOf, compileSpec, lookup_map _ _ _ hs, Option.getD_some]
    simp only [hs, hs', not_true_eq_false, ite_false, hex]
    split
    · rfl
    · rw [c.spec_row hs]
      by_cases hc : c.cands s.state ev.key = []
      · simp [hc]
      · simp only [hc, ite_false, compileRow, List.find?_map]
        have hp : ((fun b : FBranch => guardOK env b.guard) ∘ c.toF) =
            fun b => guardOK env b.guard := by funext b; rfl
        rw [hp]
        cases hb : (c.cands s.state ev.key).find? (fun b => guardOK env b.guard) with
        | none => rfl
        | some b =>
          simp only [Option.map_some]
          exact c.fire_eq hwf s hs (List.mem_of_find?_eq_some hb)
  · have hs' : s.state ∉ c.compileSpec.states := hs
    simp only [hs, hs', not_false_eq_true, ite_true]
    exact c.enterBot_eq hwf s _ ev env

/-! ## The `wf` check without building the machine -/

theorem histCands_sub (s : NodeId) : ∀ v ∈ c.histCands s, v ∈ c.leafIds := by
  intro v hv
  simp only [histCands, List.mem_filter] at hv
  exact hv.1

theorem all_dedup {α} [DecidableEq α] (l : List α) (p : α → Bool) : (dedup l).all p = l.all p := by
  apply Bool.eq_iff_iff.mpr
  simp only [List.all_eq_true, mem_dedup]

theorem mem_specRows {r : Row} (hr : r ∈ c.compileSpec.rows) :
    ∃ L ∈ c.leafIds, ∃ e, r = c.compileRow L e := by
  simp only [Machine.rows, compileSpec, List.mem_flatMap, List.mem_map] at hr
  obtain ⟨_, ⟨L, hL, rfl⟩, hr⟩ := hr
  simp only [rowsSpec, List.mem_filter, List.mem_map] at hr
  obtain ⟨⟨e, _, rfl⟩, _⟩ := hr
  exact ⟨L, hL, e, rfl⟩

theorem mem_cands_iff {L : NodeId} {e : EventId} {b : Branch} :
    b ∈ c.cands L e ↔ ∃ t ∈ c.transitions, t.src ∈ c.anc L ∧ t.event = e ∧ b ∈ t.branches := by
  simp only [cands, branchesAt, List.mem_flatMap, List.mem_filter, decide_eq_true_eq]
  constructor
  · rintro ⟨a, ha, t, ⟨ht, hs, he⟩, hb⟩
    exact ⟨t, ht, hs ▸ ha, he, hb⟩
  · rintro ⟨t, ht, ha, he, hb⟩
    exact ⟨t.src, ha, t, ⟨ht, rfl, he⟩, hb⟩

/-- The target check of the compiled machine is `destIn` of the chart's target. -/
theorem targetOK_toTarget (d : Dest) (hd : ∀ s0, d.histSec = some s0 → s0 ∈ dedup c.histSecs) :
    c.compileSpec.targetOK (c.toTarget d) = c.index.destIn (keySet c.leafIds) d := by
  induction d with
  | node n =>
    simp only [toTarget, Machine.targetOK, Index.destIn, keySet_contains, index_enter]
    rfl
  | history s0 =>
    have hm := hd s0 rfl
    show c.compileSpec.targetOK (.resume s0) = true
    simp only [Machine.targetOK, compileSpec, List.any_map, List.any_eq_true, Function.comp_def,
      mkResume]
    exact ⟨s0, hm, by simp⟩
  | back fb ih => simpa only [toTarget, Machine.targetOK, Index.destIn] using ih hd

/-- **`wfDirect` is `wf` of the compiled machine.** -/
theorem wfDirect_spec : c.compileSpec.wf = c.wfDirect := by
  have hleaf : ∀ n, (keySet c.leafIds).contains n = decide (n ∈ c.leafIds) := keySet_contains _
  have hanc : ∀ x, (keySet (c.leafIds.flatMap c.index.anc)).contains x = true ↔
      ∃ L ∈ c.leafIds, x ∈ c.anc L := by
    intro x
    rw [keySet_contains]
    simp [index_anc]
  -- third part: the initial messages of sections; the members are always messages
  have hst : c.compileSpec.states = c.leafIds := rfl
  have hrs : c.compileSpec.resumes = (dedup c.histSecs).map c.mkResume := rfl
  have h3 : (c.compileSpec.resumes.all fun r =>
        decide (r.default ∈ c.compileSpec.states) &&
          r.members.all fun v => decide (v ∈ c.compileSpec.states)) =
      c.histSecs.all (fun s => (keySet c.leafIds).contains (c.index.enter s)) := by
    apply Bool.eq_iff_iff.mpr
    simp only [List.all_eq_true, Bool.and_eq_true, decide_eq_true_eq, hst, hrs, hleaf, index_enter,
      List.mem_map, mem_dedup]
    constructor
    · intro h s hs
      exact (h (c.mkResume s) ⟨s, hs, rfl⟩).1
    · rintro h r ⟨s, hs, rfl⟩
      exact ⟨h s hs, c.histCands_sub s⟩
  -- second part: the arrow targets
  have h2 : (c.compileSpec.rows.all fun r => r.branches.all fun b => c.compileSpec.targetOK b.target) =
      c.transitions.all (fun t => !(keySet (c.leafIds.flatMap c.index.anc)).contains t.src ||
        t.branches.all (fun b => c.index.destIn (keySet c.leafIds) b.target)) := by
    apply Bool.eq_iff_iff.mpr
    simp only [List.all_eq_true, Bool.or_eq_true, Bool.not_eq_true']
    constructor
    · intro h t ht
      by_cases hs : (keySet (c.leafIds.flatMap c.index.anc)).contains t.src = true
      · right
        intro b hb
        obtain ⟨L, hL, ha⟩ := (hanc _).mp hs
        have hbc : b ∈ c.cands L t.event := c.mem_cands_iff.mpr ⟨t, ht, ha, rfl, hb⟩
        have := h _ (c.row_mem hL hbc) (c.toF b) (List.mem_map.mpr ⟨b, hbc, rfl⟩)
        rw [← c.targetOK_toTarget b.target (fun _ h => c.hist_mem hbc h)]
        exact this
      · left; simpa using hs
    · intro h r hr b' hb'
      obtain ⟨L, hL, e, rfl⟩ := c.mem_specRows hr
      simp only [compileRow, List.mem_map] at hb'
      obtain ⟨b, hb, rfl⟩ := hb'
      obtain ⟨t, ht, ha, _, hbt⟩ := c.mem_cands_iff.mp hb
      have hs : (keySet (c.leafIds.flatMap c.index.anc)).contains t.src = true :=
        (hanc _).mpr ⟨L, hL, ha⟩
      rcases h t ht with h | h
      · rw [hs] at h; cases h
      · have hd := h b hbt
        show c.compileSpec.targetOK (c.toTarget b.target) = true
        rw [c.targetOK_toTarget b.target (fun _ h => c.hist_mem hb h)]
        exact hd
  unfold Machine.wf wfDirect
  simp only [compileSpec, hleaf, index_enter] at h2 h3 ⊢
  rw [h2, h3]
  rfl

theorem wfDirect_eq : c.compile.wf = c.wfDirect := by
  rw [compile_eq_spec, wfDirect_spec]

/-- **The main theorem.** -/
theorem compile_correct (hwf : c.compile.wf = true) (s : Snapshot) (ev : Event) (env : HostEnv) :
    c.compile.step s ev env = c.step s ev env := by
  rw [compile_eq_spec] at hwf ⊢
  exact c.compileSpec_correct hwf s ev env

theorem start_correct : c.compile.start = c.start := by
  rw [compile_eq_spec]; rfl

/-- **The sections that "back" compares are those of the chart**: the section of a message in
the compiled machine is the section that directly contains it in the chart. -/
theorem compile_zoneOf {L : NodeId} (hL : L ∈ c.leafIds) : c.compile.zoneOf L = c.parent L := by
  rw [compile_eq_spec]; exact c.spec_zoneOf hL

/-- **Entering the bot with a first event**: the compiled machine agrees with the chart. -/
theorem startWith_correct (hwf : c.compile.wf = true) (n : Nat) (ev : Event) (env : HostEnv) :
    c.compile.startWith n ev env = c.startWith n ev env := by
  rw [compile_eq_spec] at hwf ⊢
  exact c.enterBot_eq hwf _ [] ev env

/-- The first-entry actions of a compiled machine are the entry actions of its initial message,
the hypothesis of `Machine.startWith_initial`. -/
theorem compile_initActions (hwf : c.compile.wf = true) :
    c.compile.entryOf c.compile.initial = c.compile.initActions := by
  rw [compile_eq_spec] at hwf ⊢
  have hi : c.enter c.root ∈ c.leafIds := (Machine.wf_parts hwf).1
  simp only [Machine.entryOf, compileSpec, lookup_map _ _ _ hi, Option.getD_some, initActs]

end Chart
end Figram

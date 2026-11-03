/-
Audit 5 (formal), item 2: the new and changed pinned theorems of 0.3.0 instantiated on the
compiled example documents of `Figram/Examples.lean` (`pizza`, `features`). Every hypothesis is
discharged by `decide` (kernel evaluation through `compile_eq_spec`, no `native_decide`), so the
theorems are not vacuous on compiled machines in the situations docs/proofs.md describes.
Run: lake env lean /home/user/figram-audit/agents5/formal/Inst.lean
-/
import Figram
open Figram Figram.Format Figram.Engine Figram.Examples

set_option maxRecDepth 100000
set_option maxHeartbeats 0

namespace Audit5.Inst

def noEnv : HostEnv := fun _ => false
def vipEnv : HostEnv := fun n => n == "is_vip"

/-! ### The compiled `features` machine is well formed (hypothesis of `step_outcome_once`). -/
theorem features_wf : features.compile.wf = true := by rw [Chart.compile_eq_spec]; decide
theorem pizza_wf : pizza.compile.wf = true := by rw [Chart.compile_eq_spec]; decide

/-! ### `startWith_eq_step` with a timer on the initial message (`home` has `idle`). -/
theorem home_initial : features.compile.initial = "home" := by rw [Chart.compile_eq_spec]; decide
def bVip : FBranch := ⟨some "is_vip", .node "vip", []⟩
theorem choose_go : features.compile.choose features.compile.initial (.named "btn:go") vipEnv = some bVip := by
  rw [Chart.compile_eq_spec]; decide
theorem exit_home : features.compile.exitOf features.compile.initial = [.cancelTimer "idle"] := by
  rw [Chart.compile_eq_spec]; decide

example :=
  Machine.startWith_eq_step features.compile 7 "btn:go" vipEnv bVip
    (by rw [Chart.compile_eq_spec]; decide) choose_go (by decide)
    (by rw [exit_home]; intro a ha; simp at ha; exact ⟨_, ha⟩)
-- the conclusion is not trivial: the step's reply has the extra `cancel_timer idle`
example : (features.compile.startWith 7 (.named "btn:go") vipEnv).2 =
    [.startTimer "nudge" 30000 8, .send "vip"] := by rw [Chart.compile_eq_spec]; decide

/-! ### `startWith_initial`: a named arrow into the initial message, and a variant without a
target (`btn:log` on `home`), which `startWith_initial` covers as well (resolve of `.stay`). -/
def bStart : FBranch := ⟨none, .node "home", []⟩
def bLog : FBranch := ⟨none, .stay, [.call "log" true .free 0]⟩
example :=
  Machine.startWith_initial features.compile 0 "cmd:start" noEnv bStart
    (by rw [Chart.compile_eq_spec]; decide) (by rw [Chart.compile_eq_spec]; decide)
    (Chart.compile_initActions _ features_wf)
example :=
  Machine.startWith_initial features.compile 0 "btn:log" noEnv bLog
    (by rw [Chart.compile_eq_spec]; decide) (by rw [Chart.compile_eq_spec]; decide)
    (Chart.compile_initActions _ features_wf)

/-! ### `startWith_unmatched` — and the outcome case it does NOT cover: the root of `features`
has an arrow `call:ping:pong`, so `choose` at the initial message is `some` for that outcome;
only `startWith_outcome` gives the plain first entry there (docs/proofs.md row of
`startWith_unmatched` says "(or for a timer or an outcome event)"). -/
example :=
  Machine.startWith_unmatched features.compile 0 (.named "btn:nothing") noEnv
    (by rw [Chart.compile_eq_spec]; decide)
example : (features.compile.choose features.compile.initial (.outcome "ping" "pong" 1) noEnv).isSome = true := by
  rw [Chart.compile_eq_spec]; decide
example := Machine.startWith_outcome features.compile 0 "ping" "pong" 1 noEnv

/-! ### `enterBot_eq_realize_some` / `_none` in recovery with an outcome (golden
`outcome_recovery` / `outcome_recovery_stale`): an accepted outcome is matched at the initial
message; one without a wait chooses nothing. -/
def sRec : Snapshot := ⟨"deleted_message", [], [], 6, none, [("ping", 6), ("check_stock", 5)]⟩
def bPong : FBranch := ⟨none, .stay, [.call "log" true .free 0]⟩
example :=
  Machine.enterBot_eq_realize_some features.compile sRec [] (.outcome "ping" "pong" 6) noEnv bPong
    (by decide) (by rw [Chart.compile_eq_spec]; decide)
example :=
  Machine.enterBot_eq_realize_none features.compile sRec [] (.outcome "ping" "pong" 5) noEnv
    (Or.inl (by decide))
-- the accepted outcome removed its wait; the other wait survives recovery
example : (features.compile.step sRec (.outcome "ping" "pong" 6) noEnv).1.pending = [("check_stock", 5)] := by
  rw [Chart.compile_eq_spec]; decide

/-! ### `step_stay` with a timer firing handled without a move (`vip`, `timer:nudge`), and
`stay_keeps` on its actions; the timer is consumed. -/
def sVip : Snapshot := ⟨"vip", [], [("nudge", 3)], 3, none, []⟩
def rNudge : Row := ⟨"vip", "timer:nudge", [bLog]⟩
example :=
  Machine.step_stay features.compile sVip (.timer "nudge" 3) noEnv rNudge bLog
    (by rw [Chart.compile_eq_spec]; decide) (by decide) (by rw [Chart.compile_eq_spec]; decide)
    (by rw [Chart.compile_eq_spec]; decide) (by decide) rfl
example :=
  Machine.stay_keeps sVip (.timer "nudge" 3) bLog.actions
    (by intro a ha; simp [bLog] at ha; exact Or.inl ⟨_, _, _, _, ha⟩)
example : (features.compile.step sVip (.timer "nudge" 3) noEnv) =
    (⟨"vip", [], [], 4, none, []⟩, [.call "log" true .free 4]) := by rw [Chart.compile_eq_spec]; decide

/-! ### `enterBot_stay` on `start` with the event of a variant without a target. -/
example :=
  Machine.enterBot_stay features.compile ⟨"", [], [], 0, none, []⟩ [] (.named "btn:log") noEnv bLog
    (by decide) (by rw [Chart.compile_eq_spec]; decide) rfl

/-! ### Waits: `step_rejected`, `step_no_arrow`, `step_keeps_wait`, `step_records_wait`,
`step_latest_replaces`, `step_outcome_once` on `features`. -/
def sItem : Snapshot := ⟨"item", [("shop", "item")], [("remind", 5)], 5, none, [("ping", 3), ("check_stock", 5)]⟩
-- an outcome with a stale token is rejected
example :=
  Machine.step_rejected features.compile sItem (.outcome "check_stock" "yes" 4) noEnv
    (by rw [Chart.compile_eq_spec]; decide) (by decide)
-- `item2` has no arrow for `call:check_stock:yes` (nor its sections): only the wait goes
def sItem2 : Snapshot := { sItem with state := "item2", timers := [] }
example :=
  Machine.step_no_arrow features.compile sItem2 (.outcome "check_stock" "yes" 5) noEnv
    (by rw [Chart.compile_eq_spec]; decide) (by rw [Chart.compile_eq_spec]; decide)
-- a move (item → item2) keeps the wait of `ping`
theorem reply_other : (features.compile.step sItem (.named "btn:other") noEnv).2 =
    [.cancelTimer "remind", .send "item2"] := by rw [Chart.compile_eq_spec]; decide
example :=
  Machine.step_keeps_wait features.compile sItem (.named "btn:other") noEnv (p := ("ping", 3))
    (by decide) (by decide) (by intro d t; rw [reply_other]; simp)
-- a new call of `check_stock` (latest only) carries the step's token and replaces the wait
theorem reply_check : (features.compile.step sItem (.named "btn:check") noEnv).2 =
    [.call "check_stock" false .latest 6] := by rw [Chart.compile_eq_spec]; decide
example :=
  Machine.step_records_wait features.compile sItem (.named "btn:check") noEnv
    (n := "check_stock") (d := false) (w := .latest) (t := 6) (by rw [reply_check]; simp)
example : ∀ t', ("check_stock", t') ∈ (features.compile.step sItem (.named "btn:check") noEnv).1.pending → t' = 6 :=
  fun t' => Machine.step_latest_replaces features.compile sItem (.named "btn:check") noEnv
    (n := "check_stock") (d := false) (t := 6) (by rw [reply_check]; simp) (t' := t')
example : (features.compile.step sItem (.named "btn:check") noEnv).1.pending = [("check_stock", 6), ("ping", 3)] := by
  rw [Chart.compile_eq_spec]; decide
-- the hypothesis `tok ≤ s.seq` of `step_outcome_once` holds for every accepted outcome of a
-- fresh snapshot (`accepts_le`), here the wait `check_stock`/5
theorem sItem_fresh : sItem.Fresh := by
  refine ⟨?_, ?_⟩ <;> intro p hp <;> simp [sItem] at hp <;> rcases hp with rfl | rfl <;> decide
example :=
  Machine.step_outcome_once features.compile features_wf sItem "check_stock" "yes" "no" 5 noEnv noEnv
    (sItem_fresh.accepts_le (c := "check_stock") (o := "yes") (by decide))

/-! ### Protocol: `decEvent_outcome`, `handle_restart`, `restart_pending` instantiated. -/
def kvsOut : Format.Obj := [("op", .str "step"), ("event", .str "call:create_order:created"), ("token", .num 6 0)]
example :=
  decEvent_outcome kvsOut "create_order" "created" 6 (by decide) (by decide) (by decide) (by decide) rfl rfl


/-! ### `toChart_decls`: its hypothesis holds whenever only the texts and annotations of the
actions change (general, for every document). -/
theorem callOpts_retext (calls : List (String × CallDecl)) (f : String → String) (g : String → Obj) :
    callOpts (calls.map fun p => (p.1, { p.2 with text := f p.1, ext := g p.1 })) = callOpts calls := by
  funext n
  simp only [callOpts, lookup]
  induction calls with
  | nil => rfl
  | cons p ps ih =>
    simp only [List.map_cons, List.find?_cons]
    by_cases h : p.1 = n
    · simp [h, CallDecl.wait]
    · simpa [h] using ih

example (d : Doc) (f : String → String) (g : String → Obj) :=
  toChart_decls d [] [] (d.calls.map fun p => (p.1, { p.2 with text := f p.1, ext := g p.1 }))
    (callOpts_retext _ f g)

end Audit5.Inst

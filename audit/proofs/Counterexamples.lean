/-
Counterexamples: what breaks when a hypothesis of a project theorem is dropped,
and where the proved model differs from the shipped text/protocol layer.
Kernel facts are `example ... := by decide` / `rfl`; facts about `partial`
functions (parse/print/check) can only be RUNTIME checks (`#guard`), because
partial defs are opaque constants in the logic.
Run: lake env lean audit/proofs/Counterexamples.lean
-/
import Figram
open Figram Figram.Format Figram.Engine

namespace Figram.Audit.CE

def noEnv : HostEnv := fun _ => false

/-! ## CE1: drop `c.compile.wf` → `compile_correct` and `step_state_valid` fail -/

/-- Root section "" with one message "a"; arrow from "a" to a node that does not exist. -/
def badChart : Chart where
  nodes := [⟨"", none, .sec "a"⟩, ⟨"a", some "", .message⟩]
  root := ""
  transitions := [⟨"a", "go", [⟨none, .node "ghost", []⟩]⟩]
  timers := []

def s0 : Snapshot := ⟨"a", [], [], 0⟩

example : badChart.compile.wf = false := by decide
-- compiled step ≠ reference step (the reference sends "ghost", compiled sends nothing)
example : badChart.compile.step s0 (.named "go") noEnv ≠ badChart.step s0 (.named "go") noEnv := by
  decide
example : badChart.step s0 (.named "go") noEnv = (⟨"ghost", [], [], 1⟩, [.send "ghost"]) := by decide
example : badChart.compile.step s0 (.named "go") noEnv = (⟨"ghost", [], [], 1⟩, []) := by decide
-- user lands on a state that does not exist
example : (badChart.compile.step s0 (.named "go") noEnv).1.state ∉ badChart.compile.states := by
  decide
-- the production path is protected dynamically: lint (part of `check`) rejects it
#guard !badChart.lint.isEmpty

/-! ## CE2: decision 7 ("timer fires only with the current token") is NOT a property of
the Core/Chart model: a `.named` event with the reserved key `timer:t` bypasses the
token check. Only `Engine.decEvent` prevents it (no project theorem says so). -/

def timerDoc : Doc where
  requires := []
  conditions := []
  calls := []
  variables := []
  bot := .sec "a" [
      ("a", .message [("timer:t", [⟨none, "b", false, [], []⟩])] [("t", 1000)] []),
      ("b", .message [] [] [])] [] []
  ext := []

def tm : Machine := timerDoc.toChart.compile

-- hypotheses of the theorems are satisfiable (non-vacuity)
example : tm.wf = true := by decide
example : namesOK tm = true := by decide
#guard timerDoc.check.isEmpty

def sA : Snapshot := ⟨"a", [], [("t", 1)], 1⟩   -- timer t active, token 1
def sA0 : Snapshot := ⟨"a", [], [], 1⟩          -- no active timer at all

example : tm.step sA (.timer "t" 7) noEnv = (sA, []) := by decide            -- stale token ignored
example : (tm.step sA (.timer "t" 1) noEnv).1.state = "b" := by decide       -- current token fires
example : (tm.step sA0 (.timer "t" 1) noEnv) = (sA0, []) := by decide        -- no timer → ignored
-- …but a NAMED event with the same key fires without any token, even with no active timer:
example : (tm.step sA0 (.named "timer:t") noEnv).1.state = "b" := by decide
example : (timerDoc.toChart.step sA0 (.named "timer:t") noEnv).1.state = "b" := by decide
-- Engine.decEvent refuses a `timer:` event without a token (runtime check)
#guard (decEvent [("event", .str "timer:t")]).toBool == false
#guard (decEvent [("event", .str "timer:t"), ("token", .num 1 0)]).toBool == true

/-! ## CE3: drop `DocWF` → `encode_injective` and `decode_encode` fail -/

def dA : Doc := ⟨[], [], [], [], .sec "a" [("a", .message [("e", [])] [] [])] [] [], []⟩
/-- same JSON, but the arrows are hidden in an "annotation" that uses the format key `on` -/
def dB : Doc := ⟨[], [], [], [], .sec "a" [("a", .message [] [] [("on", .obj [("e", .arr [])])])] [] [], []⟩

example : encode dA = encode dB := rfl
example : dA ≠ dB := by intro h; simp [dA, dB] at h
example : dA.wfOK = true := by decide
example : dB.wfOK = false := by decide
example : decode (encode dB) = .ok dA := by
  rw [show encode dB = encode dA from rfl]
  exact decode_encode dA (wfOK_sound dA (by decide))
-- …and different behaviour: dA has an arrow, dB has none
example : dA.toChart.transitions.length = 1 ∧ dB.toChart.transitions.length = 0 := by decide
#guard !dB.check.isEmpty   -- `check` would reject dB

/-! ## CE4: `check_roundtrip` is about the JSON model; the TEXT round trip can fail for a
document that passes `check` (annotation number whose normalised exponent is < -1000). -/

def expText : String :=
  "{\"figram\": 1, \"bot\": {\"initial\": \"a\", \"children\": {\"a\": {}}}, \"x-scale\": 1.5e-1000}"

-- parses, decodes, passes check:
#guard match parse expText >>= decode with
  | .ok d => d.check.isEmpty
  | .error _ => false
-- but the canonical text written by `print (encode d)` (what `figram fmt` writes) is rejected:
#guard match parse expText >>= decode with
  | .ok d => (match parse (print (encode d)) with | .ok _ => false | .error _ => true)
  | .error _ => false
#eval match parse expText >>= decode with
  | .ok d => print (encode d) ++ "\n=> " ++ (match parse (print (encode d)) with
      | .ok _ => "parsed" | .error e => "parse error: " ++ e)
  | .error e => e

/-! ## CE5: `SnapOK` has no key-uniqueness: `decSnapshot_encSnapshot` holds on the model
for a snapshot with duplicate timer names, while the text form is rejected by `parse`.
(Not reachable from the engine: `assign`/`erase` keep keys unique and `parse` rejects
duplicate keys — but no theorem states it.) -/

def dupSnap : Snapshot := ⟨"a", [], [("t", 1), ("t", 2)], 2⟩

theorem dupSnap_ok : SnapOK dupSnap where
  state := by decide
  resume := by simp [dupSnap]
  timers := by intro p hp; simp [dupSnap] at hp; rcases hp with rfl | rfl <;> decide
  seq := by decide

example : decSnapshot (encSnapshot dupSnap) = .ok dupSnap := decSnapshot_encSnapshot _ dupSnap_ok
#guard match parse (renderCompact (encSnapshot dupSnap)) with | .ok _ => false | .error _ => true
#eval renderCompact (encSnapshot dupSnap)

/-! ## CE6: theorems with hypothesis `d.check = []` can never be discharged in the
logic for a concrete document: `check` contains `partial` (opaque) functions. -/

example : True := by
  fail_if_success (have : timerDoc.check = [] := by decide)
  trivial

end Figram.Audit.CE

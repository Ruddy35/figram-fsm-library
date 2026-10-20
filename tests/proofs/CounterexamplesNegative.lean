/- Negative control for Counterexamples.lean: the opposite claims must be REJECTED.
Expected: 4 errors, exit code 1. -/
import Figram
open Figram Figram.Format Figram.Engine
namespace Figram.Audit.CENeg
def noEnv : HostEnv := fun _ => false
def badChart : Chart := ⟨[⟨"", none, .sec "a"⟩, ⟨"a", some "", .message⟩], "", [⟨"a", "go", [⟨none, .node "ghost", []⟩]⟩], []⟩
def s0 : Snapshot := ⟨"a", [], [], 0⟩
example : badChart.compile.step s0 (.named "go") noEnv = badChart.step s0 (.named "go") noEnv := by
  rw [Chart.compile_eq_spec]; decide
def timerDoc : Doc := ⟨[], [], [], [], .sec "a" [("a", .message [("timer:t", [⟨none, "b", false, [], []⟩])] [("t", 1000)] []), ("b", .message [] [] [])] [] [], []⟩
def sA0 : Snapshot := ⟨"a", [], [], 1⟩
example : (timerDoc.toChart.compile.step sA0 (.named "timer:t") noEnv) = (sA0, []) := by
  rw [Chart.compile_eq_spec]; decide
def dA : Doc := ⟨[], [], [], [], .sec "a" [("a", .message [("e", [])] [] [])] [] [], []⟩
def dB : Doc := ⟨[], [], [], [], .sec "a" [("a", .message [] [] [("on", .obj [("e", .arr [])])])] [] [], []⟩
example : dB.wfOK = true := by decide
example : dA.toChart.transitions.length = dB.toChart.transitions.length := by decide
end Figram.Audit.CENeg

import Figram.Lint
import Figram.Correct

/-!
# The property of the chart check

A separate module: the check (`Figram.Lint`) is part of the embeddable library
and does not depend on the modules with proofs.
-/

namespace Figram
namespace Chart

/-- **A chart without errors compiles into a machine that passes `wf`.** -/
theorem lint_nil_wf (c : Chart) (h : c.lint = []) : c.compile.wf = true := by
  rw [wfDirect_eq]
  unfold lint at h
  cases hw : c.wfDirect with
  | true => rfl
  | false =>
    exfalso
    simp only [hw, Bool.not_false, Bool.and_true] at h
    split at h
    · simp at h
    · rename_i hc
      rw [h] at hc
      simp at hc

end Chart
end Figram

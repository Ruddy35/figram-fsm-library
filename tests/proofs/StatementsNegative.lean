/- Negative control for Statements.lean: README wording without hypotheses must be REJECTED.
Expected: 2 errors, exit code 1. Run: lake env lean tests/proofs/StatementsNegative.lean -/
import Figram
open Figram Figram.Format
-- negative control: the README wording WITHOUT the hypotheses must NOT be accepted
example : ∀ (c : Chart) (s : Snapshot) (ev : Event) (env : HostEnv),
    c.compile.step s ev env = c.step s ev env := @Chart.compile_correct
example : ∀ (d : Doc), decode (encode d) = .ok d := @decode_encode

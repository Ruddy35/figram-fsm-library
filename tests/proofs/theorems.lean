import Figram
import Lean

/-!
# The pinned theorems listed in docs/proofs.md

`verify.sh` elaborates this file (`lake env lean tests/proofs/theorems.lean`) and requires:
* the statement of every theorem (the `STMT` lines) equals `tests/proofs/THEOREMS.expected`
  — a statement can change only together with that file, deliberately;
* every theorem depends only on `propext`, `Classical.choice`, `Quot.sound`.
When adding a theorem to docs/proofs.md, add it to both lists below.
-/

open Lean Elab Command Meta in
#eval show CommandElabM Unit from do
  for n in [
      ``Figram.Chart.compile_correct,
      ``Figram.Chart.run_correct,
      ``Figram.Chart.session_correct,
      ``Figram.Chart.chart_run_valid,
      ``Figram.Machine.step_state_valid,
      ``Figram.Machine.step_guard_local,
      ``Figram.Chart.compile_eq_spec,
      ``Figram.Chart.wfDirect_eq,
      ``Figram.Chart.lint_nil_wf,
      ``Figram.Machine.wfFast_eq,
      ``Figram.Format.decode_encode,
      ``Figram.Format.encode_injective,
      ``Figram.Format.decode_requires,
      ``Figram.Format.toChart_strip,
      ``Figram.Format.toChart_decls,
      ``Figram.Format.check_roundtrip,
      ``Figram.Engine.decSnapshot_encSnapshot,
      ``Figram.Engine.step_snapOK,
      ``Figram.Engine.handle_step,
      ``Figram.Engine.reply_readable,
      ``Figram.Engine.guards_irrelevant,
      ``Figram.Engine.missing_nil_step,
      ``Figram.Engine.startAt_snapOK
    ] do
    let ci ← getConstInfo n
    let f ← liftTermElabM (ppExpr ci.type)
    logInfo m!"STMT {n} : {f.pretty 1000000}"

#print axioms Figram.Chart.compile_correct
#print axioms Figram.Chart.run_correct
#print axioms Figram.Chart.session_correct
#print axioms Figram.Chart.chart_run_valid
#print axioms Figram.Machine.step_state_valid
#print axioms Figram.Machine.step_guard_local
#print axioms Figram.Chart.compile_eq_spec
#print axioms Figram.Chart.wfDirect_eq
#print axioms Figram.Chart.lint_nil_wf
#print axioms Figram.Machine.wfFast_eq
#print axioms Figram.Format.decode_encode
#print axioms Figram.Format.encode_injective
#print axioms Figram.Format.decode_requires
#print axioms Figram.Format.toChart_strip
#print axioms Figram.Format.toChart_decls
#print axioms Figram.Format.check_roundtrip
#print axioms Figram.Engine.decSnapshot_encSnapshot
#print axioms Figram.Engine.step_snapOK
#print axioms Figram.Engine.handle_step
#print axioms Figram.Engine.reply_readable
#print axioms Figram.Engine.guards_irrelevant
#print axioms Figram.Engine.missing_nil_step
#print axioms Figram.Engine.startAt_snapOK

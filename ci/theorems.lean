import Figram
import Lean

/-!
# Теоремы, которые README.md и AUDIT.md называют доказанными

`verify.sh` обрабатывает этот файл (`lake env lean ci/theorems.lean`) и требует:
* формулировка каждой теоремы (строки `STMT`) совпадает с `ci/THEOREMS.expected`
  — изменить формулировку можно только вместе с этим файлом, осознанно;
* каждая теорема зависит только от `propext`, `Classical.choice`, `Quot.sound`.
Добавляя теорему в README или AUDIT, добавьте её в оба списка ниже.
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
      ``Figram.Engine.guards_irrelevant
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

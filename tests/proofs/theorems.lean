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
      ``Figram.Engine.startAt_snapOK,
      ``Figram.Chart.startWith_correct,
      ``Figram.Chart.sessionWith_correct,
      ``Figram.Chart.compile_initActions,
      ``Figram.Machine.enterBot_state_valid,
      ``Figram.Machine.enterBot_guard_local,
      ``Figram.Machine.startWith_eq_step,
      ``Figram.Machine.startWith_initial,
      ``Figram.Machine.startWith_unmatched,
      ``Figram.Engine.handle_start,
      ``Figram.Engine.missingStart_nil,
      ``Figram.Engine.startWith_snapOK,
      ``Figram.Machine.step_recover,
      ``Figram.Machine.step_recover_timer,
      ``Figram.Machine.fire_back,
      ``Figram.Machine.fire_back_swap,
      ``Figram.Machine.step_back,
      ``Figram.Machine.enterBot_back,
      ``Figram.Chart.compile_zoneOf,
      ``Figram.Machine.realize_clear,
      ``Figram.Machine.fire_eq_realize,
      ``Figram.Machine.enterBot_eq_realize_some,
      ``Figram.Machine.enterBot_eq_realize_none,
      ``Figram.Machine.recover_eq_realize,
      ``Figram.Machine.step_rejected,
      ``Figram.Machine.step_no_arrow,
      ``Figram.Machine.step_pending_new,
      ``Figram.Machine.step_keeps_wait,
      ``Figram.Machine.step_records_wait,
      ``Figram.Machine.step_latest_replaces,
      ``Figram.Machine.step_fresh,
      ``Figram.Machine.startWith_fresh,
      ``Figram.Snapshot.Fresh.accepts_le,
      ``Figram.Machine.step_outcome_once,
      ``Figram.Machine.step_stay,
      ``Figram.Machine.stay_keeps,
      ``Figram.Machine.enterBot_stay,
      ``Figram.Machine.startWith_outcome,
      ``Figram.Machine.step_undetach,
      ``Figram.Machine.startWith_undetach,
      ``Figram.Machine.recover_undetach,
      ``Figram.splitOutcome_outcomeEvent,
      ``Figram.Engine.decEvent_key,
      ``Figram.Engine.decEvent_outcome,
      ``Figram.Engine.decStartEvent_ok,
      ``Figram.Engine.handle_restart,
      ``Figram.Engine.restart_pending,
      ``Figram.Engine.restart_fresh,
      ``Figram.Machine.step_nodup,
      ``Figram.Machine.startWith_nodup,
      ``Figram.Machine.run_nodup,
      ``Figram.Machine.step_outcome_accepted,
      ``Figram.Machine.step_stay_timer_once,
      ``Figram.Machine.step_seq,
      ``Figram.Machine.consecutive_tokens,
      ``Figram.Machine.outcome_once_forever,
      ``Figram.Chart.step_outcome_unmatched,
      ``Figram.Chart.step_waits_once,
      ``Figram.Chart.compile_step_waits_once,
      ``Figram.Chart.restart_no_waits,
      ``Figram.Format.toChart_calls,
      ``Figram.Format.doc_stay,
      ``Figram.Format.check_waitsOnce,
      ``Figram.Format.check_outcome_events,
      ``Figram.Format.check_step_waits_once,
      ``Figram.Engine.decEvent_named_key,
      ``Figram.Engine.startAt_fresh,
      ``Figram.Snapshot.accepts_named,
      ``Figram.Snapshot.accepts_timer,
      ``Figram.Snapshot.accepts_outcome,
      ``Figram.Snapshot.settle_outcome,
      ``Figram.Snapshot.consume_timer,
      ``Figram.Snapshot.consume_other,
      ``Figram.addWait_free,
      ``Figram.addWait_latest,
      ``Figram.addWait_every,
      ``Figram.stay_eq
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
#print axioms Figram.Chart.startWith_correct
#print axioms Figram.Chart.sessionWith_correct
#print axioms Figram.Chart.compile_initActions
#print axioms Figram.Machine.enterBot_state_valid
#print axioms Figram.Machine.enterBot_guard_local
#print axioms Figram.Machine.startWith_eq_step
#print axioms Figram.Machine.startWith_initial
#print axioms Figram.Machine.startWith_unmatched
#print axioms Figram.Engine.handle_start
#print axioms Figram.Engine.missingStart_nil
#print axioms Figram.Engine.startWith_snapOK
#print axioms Figram.Machine.step_recover
#print axioms Figram.Machine.step_recover_timer
#print axioms Figram.Machine.fire_back
#print axioms Figram.Machine.fire_back_swap
#print axioms Figram.Machine.step_back
#print axioms Figram.Machine.enterBot_back
#print axioms Figram.Chart.compile_zoneOf
#print axioms Figram.Machine.realize_clear
#print axioms Figram.Machine.fire_eq_realize
#print axioms Figram.Machine.enterBot_eq_realize_some
#print axioms Figram.Machine.enterBot_eq_realize_none
#print axioms Figram.Machine.recover_eq_realize
#print axioms Figram.Machine.step_rejected
#print axioms Figram.Machine.step_no_arrow
#print axioms Figram.Machine.step_pending_new
#print axioms Figram.Machine.step_keeps_wait
#print axioms Figram.Machine.step_records_wait
#print axioms Figram.Machine.step_latest_replaces
#print axioms Figram.Machine.step_fresh
#print axioms Figram.Machine.startWith_fresh
#print axioms Figram.Snapshot.Fresh.accepts_le
#print axioms Figram.Machine.step_outcome_once
#print axioms Figram.Machine.step_stay
#print axioms Figram.Machine.stay_keeps
#print axioms Figram.Machine.enterBot_stay
#print axioms Figram.Machine.startWith_outcome
#print axioms Figram.Machine.step_undetach
#print axioms Figram.Machine.startWith_undetach
#print axioms Figram.Machine.recover_undetach
#print axioms Figram.splitOutcome_outcomeEvent
#print axioms Figram.Engine.decEvent_key
#print axioms Figram.Engine.decEvent_outcome
#print axioms Figram.Engine.decStartEvent_ok
#print axioms Figram.Engine.handle_restart
#print axioms Figram.Engine.restart_pending
#print axioms Figram.Engine.restart_fresh
#print axioms Figram.Machine.step_nodup
#print axioms Figram.Machine.startWith_nodup
#print axioms Figram.Machine.run_nodup
#print axioms Figram.Machine.step_outcome_accepted
#print axioms Figram.Machine.step_stay_timer_once
#print axioms Figram.Machine.step_seq
#print axioms Figram.Machine.consecutive_tokens
#print axioms Figram.Machine.outcome_once_forever
#print axioms Figram.Chart.step_outcome_unmatched
#print axioms Figram.Chart.step_waits_once
#print axioms Figram.Chart.compile_step_waits_once
#print axioms Figram.Chart.restart_no_waits
#print axioms Figram.Format.toChart_calls
#print axioms Figram.Format.doc_stay
#print axioms Figram.Format.check_waitsOnce
#print axioms Figram.Format.check_outcome_events
#print axioms Figram.Format.check_step_waits_once
#print axioms Figram.Engine.decEvent_named_key
#print axioms Figram.Engine.startAt_fresh
#print axioms Figram.Snapshot.accepts_named
#print axioms Figram.Snapshot.accepts_timer
#print axioms Figram.Snapshot.accepts_outcome
#print axioms Figram.Snapshot.settle_outcome
#print axioms Figram.Snapshot.consume_timer
#print axioms Figram.Snapshot.consume_other
#print axioms Figram.addWait_free
#print axioms Figram.addWait_latest
#print axioms Figram.addWait_every
#print axioms Figram.stay_eq

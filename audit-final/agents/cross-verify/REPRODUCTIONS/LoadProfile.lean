import Figram.Engine

/-!
cross-verify C6: wall time of each phase of `Engine.load` on one document.
Each phase reads its input from an IO.Ref AFTER the start timestamp and stores its
result in an IO.Ref BEFORE the end timestamp, so the (strict) pure computation
cannot be floated out of its timing window.
Usage: xverify-loadprofile DOC
-/

open Figram Figram.Format

def phase {α β} (label : String) (inp : IO.Ref α) (f : α → β) (size : β → Nat)
    (out : IO.Ref (Option β)) : IO Unit := do
  let t0 ← IO.monoNanosNow
  let x ← inp.get
  let r := f x
  out.set (some r)
  let n := match (← out.get) with | some v => size v | none => 0
  let t1 ← IO.monoNanosNow
  IO.println s!"{label}: {(t1 - t0) / 1000000} ms (size {n})"

def main (args : List String) : IO UInt32 := do
  let [path] := args | IO.eprintln "usage: xverify-loadprofile DOC"; return 2
  let txt ← IO.FS.readFile path
  let rTxt ← IO.mkRef txt
  let rJ ← IO.mkRef (none : Option (Except String J))
  phase "parse" rTxt parse (fun r => match r with | .ok _ => 1 | .error _ => 0) rJ
  let some (.ok j) := (← rJ.get) | IO.eprintln "parse error"; return 1
  let rJ2 ← IO.mkRef j
  let rD ← IO.mkRef (none : Option (Except String Doc))
  phase "decode" rJ2 decode (fun r => match r with | .ok _ => 1 | .error _ => 0) rD
  let some (.ok d) := (← rD.get) | IO.eprintln "decode error"; return 1
  let rDoc ← IO.mkRef d
  let rErrs ← IO.mkRef (none : Option (List String))
  phase "Doc.check (incl. lint -> compile.wf)" rDoc Doc.check List.length rErrs
  let rChart ← IO.mkRef d.toChart
  phase "  of which Chart.lint alone" rChart Chart.lint List.length rErrs
  let rM ← IO.mkRef (none : Option Machine)
  phase "Chart.compile (second compile in Engine.load)" rChart Chart.compile (fun m => m.rows.length) rM
  let some m := (← rM.get) | return 1
  let rM2 ← IO.mkRef m
  let rB ← IO.mkRef (none : Option Bool)
  phase "Machine.wf (second wf in Engine.load)" rM2 Machine.wf (fun b => if b then 1 else 0) rB
  phase "namesOK" rM2 Engine.namesOK (fun b => if b then 1 else 0) rB
  let rL ← IO.mkRef (none : Option (Except (List String) Machine))
  phase "TOTAL Engine.load" rTxt Engine.load (fun r => match r with | .ok m => m.states.length | .error _ => 0) rL
  return 0

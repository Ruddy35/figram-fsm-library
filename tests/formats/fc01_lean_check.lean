import Figram.FormatIO
open Figram.Format
/-! Lean-level check for FC-01: every text that `parse` accepts is printed by
`renderCompact` into a text that `parse` accepts again, with the same model value.
Run: `lake env lean tests/formats/fc01_lean_check.lean`; exit code 1 and FAIL lines if broken. -/
def cases : List String :=
  ["1.0e-1000", "-2.50e-999", "0." ++ String.ofList (List.replicate 1000 '0') ++ "1",
   "1e-1000", "1e1000", "1.5e3", "-0.0", "0.0e-0", "1E+2", "123.456e-2", "1e-31", "1e-30",
   "[1.0e-1000, {\"a\": 0.5e-1000}]"]
partial def numEq : J → J → Bool
  | .num m e, .num m' e' => m == m' && e == e'
  | .arr xs, .arr ys => xs.length == ys.length && (xs.zip ys).all (fun (a, b) => numEq a b)
  | .obj xs, .obj ys => xs.length == ys.length && (xs.zip ys).all (fun (a, b) => a.1 == b.1 && numEq a.2 b.2)
  | .str a, .str b => a == b
  | .bool a, .bool b => a == b
  | .null, .null => true
  | _, _ => false
#eval do
  let mut bad := 0
  for c in cases do
    match parse c with
    | .error e => IO.println s!"input rejected (unexpected): {c.take 20} {e}"; bad := bad + 1
    | .ok j =>
      let t := renderCompact j
      match parse t with
      | .ok j' => unless numEq j j' do IO.println s!"FAIL value changed: {c.take 20}"; bad := bad + 1
      | .error e => IO.println s!"FAIL {c.take 20} -> {t.take 30}: {e}"; bad := bad + 1
  IO.println s!"cases {cases.length}, failures {bad}"
  if bad > 0 then throw (IO.userError s!"FC-01: {bad} printed numbers are not readable")

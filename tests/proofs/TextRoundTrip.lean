/-
Regression test (runtime, `#guard`): text round trip of numbers in annotations.
A document that passes `check` must be printed (`print ∘ encode`, i.e. `figram fmt`)
into text that `parse` accepts and that decodes to the same document model.
Baseline: FAILS (1.5e-1000 is printed as "15e-1001", exponent > maxExponent).
Run: lake env lean tests/proofs/TextRoundTrip.lean   (exit 0 = pass)
-/
import Figram
open Figram Figram.Format

def docWith (num : String) : String :=
  "{\"figram\": 1, \"bot\": {\"initial\": \"a\", \"children\": {\"a\": {\"x-n\": " ++ num ++ "}}}, \"x-scale\": " ++ num ++ "}"

/-- `true` iff the document parses, passes `check`, and `parse (print (encode d))`
decodes to a document with the same canonical print and the same annotation values. -/
def textRoundTrips (num : String) : Bool :=
  match parse (docWith num) >>= decode with
  | .error _ => false
  | .ok d =>
    d.check.isEmpty &&
    match parse (print (encode d)) >>= decode with
    | .ok d' => print (encode d') == print (encode d) &&
        (match d.ext, d'.ext with
         | [(_, .num m e)], [(_, .num m' e')] => m == m' && e == e'
         | _, _ => false)
    | .error _ => false

def cases : List String :=
  ["1.5e-1000", "-2.5e-1000", "0.0001e-999", "1e-1000", "1.000e-998", "0e-1000", "0.0e-1000",
   "1e1000", "1.5e1000", "123.456", "1e-31", "15e-40", "0.5", "-0", "7.2e6"]

#eval cases.map fun c => (c, textRoundTrips c)
#guard cases.all textRoundTrips

import Figram.Format

/-!
# JSON text ↔ the model `J`

A strict RFC 8259 parser with two additional rules:

* a duplicate key in an object is an error (the RFC allows it, the format does not:
  otherwise two readers could understand a document differently);
* the absolute value of a number's exponent is at most `maxExponent`, a protection
  against inputs such as `1e1000000000` that would otherwise need huge computations.

Otherwise numbers are anything the RFC 8259 grammar allows: negative, fractional,
with an exponent. The number model is exact: `m · 10^e`. The limits of format fields
(timer delays are integers from 0 to 2^53 − 1) are checked by the decoder and `check`.

Parsing works on the UTF-8 bytes of the string itself (a position is a byte index),
without an intermediate list of characters: the memory is the text and the built value.
Duplicate keys are looked up in a set of the object's keys (O(1) per key on average),
the digits of a number are converted to `Nat` by halving (not digit by digit), and
numbers are printed the same way, so the time is almost linear in the length of the text.

Printing keeps the key order of the model; the indent is two spaces. Numbers with a
large negative exponent are printed in exponent notation if the absolute value of the
exponent is at most `maxExponent`, positionally otherwise: everything printed is read
back by `parse` into the same model value.
This layer is not covered by proofs; it is deliberately small.
-/

namespace Figram.Format

abbrev P := Except String

/-- The largest allowed absolute value of the exponent in the text of a number. -/
def maxExponent : Nat := 1000

/-- The largest nesting depth of arrays and objects. Bounds the recursion of the
parser, the printer and the checks equally in every environment (Go thread, Python, CLI). -/
def maxDepth : Nat := 256

/-! ## Parsing: a position is a UTF-8 byte index in the source string -/

/-- The byte of the text at position `i`; 0 past the end of the text (where it matters,
the caller first checks `i < s.utf8ByteSize`). -/
@[inline] def byteAt (s : String) (i : Nat) : UInt8 :=
  if h : i < s.utf8ByteSize then s.getUTF8Byte ⟨i⟩ h else 0

/-- Position `i` holds byte `b`. -/
@[inline] def byteIs (s : String) (i : Nat) (b : UInt8) : Bool :=
  i < s.utf8ByteSize && byteAt s i == b

@[inline] def isDigitB (b : UInt8) : Bool := 48 ≤ b && b ≤ 57

/-- Position `i` holds a decimal digit. -/
@[inline] def digitAt (s : String) (i : Nat) : Bool :=
  i < s.utf8ByteSize && isDigitB (byteAt s i)

/-- The character starting at position `i` (for an error text). -/
def charAt (s : String) (i : Nat) : Char := String.Pos.Raw.get s ⟨i⟩

partial def skipWs (s : String) (i : Nat) : Nat :=
  if h : i < s.utf8ByteSize then
    let b := s.getUTF8Byte ⟨i⟩ h
    if b == 32 || b == 10 || b == 13 || b == 9 then skipWs s (i + 1) else i
  else i

def hexValB (b : UInt8) : Option Nat :=
  if 48 ≤ b && b ≤ 57 then some (b - 48).toNat
  else if 97 ≤ b && b ≤ 102 then some (b - 87).toNat
  else if 65 ≤ b && b ≤ 70 then some (b - 55).toNat
  else none

/-- The text from position `i` (a character boundary) has at least `k` characters. -/
partial def hasChars (s : String) (i k : Nat) : Bool :=
  if k = 0 then true
  else if i < s.utf8ByteSize then hasChars s (String.Pos.Raw.next s ⟨i⟩).byteIdx (k - 1)
  else false

/-- Four hexadecimal digits after `\u`. Fewer than four characters before the end of
the text means "truncated", otherwise any non-hexadecimal character means "invalid". -/
def hex4 (s : String) (i : Nat) : P Nat :=
  if !hasChars s i 4 then .error "обрыв последовательности \\u"
  else
    match hexValB (byteAt s i), hexValB (byteAt s (i + 1)), hexValB (byteAt s (i + 2)),
      hexValB (byteAt s (i + 3)) with
    | some a, some b, some c, some d => .ok (((a * 16 + b) * 16 + c) * 16 + d)
    | _, _, _, _ => .error "неверная последовательность \\u"

/-- The end of a stretch of the string without a quote, a backslash or control characters. -/
partial def plainEnd (s : String) (i : Nat) : Nat :=
  if h : i < s.utf8ByteSize then
    let b := s.getUTF8Byte ⟨i⟩ h
    if b == 34 || b == 92 || b < 32 then i else plainEnd s (i + 1)
  else i

/-- A string after the opening quote: the value and the position after the closing quote.
Stretches without escape sequences are copied as a whole. -/
partial def parseStr (s : String) (acc : String) (i : Nat) : P (String × Nat) := do
  let j := plainEnd s i
  let acc := if i < j then acc ++ String.Pos.Raw.extract s ⟨i⟩ ⟨j⟩ else acc
  if h : j < s.utf8ByteSize then
    let b := s.getUTF8Byte ⟨j⟩ h
    if b == 34 then pure (acc, j + 1)
    else if b == 92 then
      if h2 : j + 1 < s.utf8ByteSize then
        let e := s.getUTF8Byte ⟨j + 1⟩ h2
        if e == 34 then parseStr s (acc.push '"') (j + 2)
        else if e == 92 then parseStr s (acc.push '\\') (j + 2)
        else if e == 47 then parseStr s (acc.push '/') (j + 2)
        else if e == 98 then parseStr s (acc.push '\x08') (j + 2)
        else if e == 102 then parseStr s (acc.push '\x0c') (j + 2)
        else if e == 110 then parseStr s (acc.push '\n') (j + 2)
        else if e == 114 then parseStr s (acc.push '\r') (j + 2)
        else if e == 116 then parseStr s (acc.push '\t') (j + 2)
        else if e == 117 then
          let hi ← hex4 s (j + 2)
          if 0xD800 ≤ hi && hi < 0xDC00 then
            if byteIs s (j + 6) 92 && byteIs s (j + 7) 117 then
              let lo ← hex4 s (j + 8)
              if 0xDC00 ≤ lo && lo < 0xE000 then
                parseStr s (acc.push (Char.ofNat (0x10000 + (hi - 0xD800) * 0x400 + (lo - 0xDC00)))) (j + 12)
              else throw "непарный суррогат в \\u"
            else throw "непарный суррогат в \\u"
          else if 0xDC00 ≤ hi && hi < 0xE000 then throw "непарный суррогат в \\u"
          else parseStr s (acc.push (Char.ofNat hi)) (j + 6)
        else throw s!"неизвестная escape-последовательность \\{charAt s (j + 1)}"
      -- a backslash as the last character of the text
      else throw "незакрытая строка"
    else throw "управляющий символ в строке"
  else throw "незакрытая строка"

/-- The end of a run of digits starting at `i`. -/
partial def digitsEnd (s : String) (i : Nat) : Nat :=
  if digitAt s i then digitsEnd s (i + 1) else i

/-- The value of at most 18 consecutive digits. -/
partial def digitsSmall (s : String) (i b acc : Nat) : Nat :=
  if i < b then digitsSmall s (i + 1) b (acc * 10 + (byteAt s i - 48).toNat) else acc

/-- The value of the digits in bytes `[a, b)`: by halving, so the time is that of
big-number multiplication, not quadratic in the number of digits. -/
partial def digitsVal (s : String) (a b : Nat) : Nat :=
  if b - a ≤ 18 then digitsSmall s a b 0
  else
    let k := (b - a) / 2
    digitsVal s a (b - k) * 10 ^ k + digitsVal s (b - k) b

/-- The value of the exponent, saturated at `maxExponent + 1`: larger values need not
be told apart, and leading zeros of any length do not change the value. -/
partial def expVal (s : String) (i b acc : Nat) : Nat :=
  if i < b then expVal s (i + 1) b (min (acc * 10 + (byteAt s i - 48).toNat) (maxExponent + 1))
  else acc

/-- A JSON number by the RFC 8259 grammar from position `i` → the exact `m · 10^e` and
the position after the number. -/
def parseNum (s : String) (i : Nat) : P (Int × Int × Nat) := do
  let neg := byteIs s i 45
  let a := if neg then i + 1 else i
  let ie := digitsEnd s a
  if ie = a then throw "ожидалась цифра"
  let (fa, fe) ← if byteIs s ie 46 then
      let fe := digitsEnd s (ie + 1)
      if fe = ie + 1 then throw "после «.» нужна цифра" else pure (ie + 1, fe)
    else pure (ie, ie)
  let (ex, j) ← if byteIs s fe 101 || byteIs s fe 69 then
      let k := fe + 1
      let (eneg, k) := if byteIs s k 45 then (true, k + 1)
        else if byteIs s k 43 then (false, k + 1) else (false, k)
      let ke := digitsEnd s k
      if ke = k then throw "в показателе нужна цифра"
      let ev := expVal s k ke 0
      if ev > maxExponent then throw s!"показатель степени по модулю больше {maxExponent}"
      pure ((if eneg then -(ev : Int) else ev), ke)
    else pure (0, fe)
  let fc := fe - fa
  let m : Int := digitsVal s a ie * 10 ^ fc + digitsVal s fa fe
  pure ((if neg then -m else m), ex - fc, j)

/-- The set of an object's keys for the duplicate check: buckets by hash. -/
structure KeySet where
  count : Nat
  buckets : Array (List String)

/-- An empty set without buckets: for small objects (up to 8 keys) a duplicate is
searched linearly among the keys read so far, and no set is built. -/
def KeySet.none : KeySet := ⟨0, #[]⟩

@[inline] def KeySet.slot (n : Nat) (k : String) : Nat := (hash k % n.toUInt64).toNat

def KeySet.grow (ks : KeySet) : KeySet :=
  let n := ks.buckets.size * 2
  ⟨ks.count, ks.buckets.foldl (fun acc l => l.foldl (fun acc k => acc.modify (KeySet.slot n k) (k :: ·)) acc)
    (Array.replicate n [])⟩

/-- `(true, ks)` if the key is already there, otherwise `(false, ks ∪ {k})`. At least one bucket. -/
def KeySet.insert (ks : KeySet) (k : String) : Bool × KeySet :=
  let i := KeySet.slot ks.buckets.size k
  if (ks.buckets[i]!).contains k then (true, ks)
  else
    let ks : KeySet := ⟨ks.count + 1, ks.buckets.modify i (k :: ·)⟩
    (false, if ks.count * 4 > ks.buckets.size * 3 then ks.grow else ks)

/-- Whether key `k` repeats among the pairs read so far (`acc`): a linear search up to
8 keys, then the set `keys` (built once from the keys read so far). -/
def dupKey (acc : Array (String × J)) (keys : KeySet) (k : String) : Bool × KeySet :=
  if acc.size < 8 then (acc.any (·.1 == k), keys)
  else
    let keys := if keys.buckets.isEmpty then
      acc.foldl (fun ks p => (ks.insert p.1).2) ⟨0, Array.replicate 32 []⟩ else keys
    keys.insert k

mutual
/-- A JSON value from position `i` (whitespace before it is skipped) and the position after it. -/
partial def parseValue (s : String) (depth : Nat) (i : Nat) : P (J × Nat) :=
  let i := skipWs s i
  if h : i < s.utf8ByteSize then
    let b := s.getUTF8Byte ⟨i⟩ h
    if b == 123 then
      if depth ≥ maxDepth then .error s!"вложенность больше {maxDepth}"
      else parseObj s (depth + 1) #[] KeySet.none (skipWs s (i + 1)) true
    else if b == 91 then
      if depth ≥ maxDepth then .error s!"вложенность больше {maxDepth}"
      else parseArr s (depth + 1) #[] (skipWs s (i + 1)) true
    else if b == 34 then do
      let (str, j) ← parseStr s "" (i + 1)
      pure (.str str, j)
    else if b == 116 && byteIs s (i + 1) 114 && byteIs s (i + 2) 117 && byteIs s (i + 3) 101 then
      .ok (.bool true, i + 4)
    else if b == 102 && byteIs s (i + 1) 97 && byteIs s (i + 2) 108 && byteIs s (i + 3) 115
        && byteIs s (i + 4) 101 then
      .ok (.bool false, i + 5)
    else if b == 110 && byteIs s (i + 1) 117 && byteIs s (i + 2) 108 && byteIs s (i + 3) 108 then
      .ok (.null, i + 4)
    else if isDigitB b || b == 45 then
      if b == 48 && digitAt s (i + 1) then .error "ведущие нули запрещены"
      else if b == 45 && byteIs s (i + 1) 48 && digitAt s (i + 2) then .error "ведущие нули запрещены"
      else do
        let (m, e, j) ← parseNum s i
        pure (.num m e, j)
    else .error s!"неожиданный символ «{charAt s i}»"
  else .error "неожиданный конец текста"
partial def parseArr (s : String) (depth : Nat) (acc : Array J) (i : Nat) (first : Bool) :
    P (J × Nat) :=
  if byteIs s i 93 then
    if first then .ok (.arr [], i + 1) else .error "лишняя запятая перед «]»"
  else do
    let (v, j) ← parseValue s depth i
    let j := skipWs s j
    if byteIs s j 44 then parseArr s depth (acc.push v) (skipWs s (j + 1)) false
    else if byteIs s j 93 then pure (.arr (acc.push v).toList, j + 1)
    else throw "ожидалась «,» или «]»"
partial def parseObj (s : String) (depth : Nat) (acc : Array (String × J)) (keys : KeySet)
    (i : Nat) (first : Bool) : P (J × Nat) :=
  if byteIs s i 125 then
    if first then .ok (.obj [], i + 1) else .error "лишняя запятая перед «}»"
  else if byteIs s i 34 then do
    let (k, j) ← parseStr s "" (i + 1)
    let (dup, keys) := dupKey acc keys k
    if dup then throw s!"повторяющийся ключ «{k}»"
    let j := skipWs s j
    if byteIs s j 58 then
      let (v, j) ← parseValue s depth (j + 1)
      let j := skipWs s j
      if byteIs s j 44 then parseObj s depth (acc.push (k, v)) keys (skipWs s (j + 1)) false
      else if byteIs s j 125 then pure (.obj (acc.push (k, v)).toList, j + 1)
      else throw "ожидалась «,» или «}»"
    else throw "ожидалось «:»"
  else .error "ожидался ключ в кавычках"
end

def parse (s : String) : P J := do
  let (v, i) ← parseValue s 0 0
  if skipWs s i < s.utf8ByteSize then throw "лишний текст после JSON" else pure v

/-! ## Printing -/

/-- The decimal notation of a natural number. Large numbers are split in half by a
power of ten (`Nat.repr` converts them digit by digit, in time quadratic in the
length of the notation). -/
partial def natDec (n : Nat) : String :=
  if n < 1000000000000000000 then Nat.repr n
  else
    -- k ≈ half the number of digits; 10^k ≤ √n, so the high part is not zero
    let k := n.log2 * 3 / 20
    let p := 10 ^ k
    let lo := natDec (n % p)
    natDec (n / p) ++ "".pushn '0' (k - lo.utf8ByteSize) ++ lo

/-- The decimal notation of an integer, like `toString` for `Int`. -/
def intDec : Int → String
  | .ofNat n => natDec n
  | .negSucc n => "-" ++ natDec (n + 1)

def hexDigit (n : Nat) : Char :=
  if n < 10 then Char.ofNat ('0'.toNat + n) else Char.ofNat ('a'.toNat + n - 10)

/-- `\u00XX` for a code below 256. -/
def u00 (acc : String) (n : Nat) : String :=
  ((acc ++ "\\u00").push (hexDigit (n / 16))).push (hexDigit (n % 16))

def escape (s : String) : String :=
  s.foldl (fun acc c =>
    match c with
    | '"' => acc ++ "\\\""
    | '\\' => acc ++ "\\\\"
    | '\n' => acc ++ "\\n"
    | '\r' => acc ++ "\\r"
    | '\t' => acc ++ "\\t"
    | c => if c.toNat < 0x20 then u00 acc c.toNat else acc.push c) ""

/-- A text for diagnostics on a terminal or in a log: control characters (C0, DEL, C1)
are replaced with `\u00XX`, so a name from a document cannot start a new line
or send an escape sequence to the terminal. -/
def sanitize (s : String) : String :=
  s.foldl (fun acc c =>
    if c.toNat < 0x20 || (0x7f ≤ c.toNat && c.toNat < 0xa0) then u00 acc c.toNat
    else acc.push c) ""

partial def render (ind : String) : J → String
  | .null => "null"
  | .bool b => if b then "true" else "false"
  | .num m e =>
    if e = 0 then intDec m
    -- The exponent in exponent notation is at most `maxExponent` in absolute value:
    -- otherwise `parse` could not read the output back. Such `e` occur only for numbers
    -- with a fractional part (`1.0e-1000` → `10 · 10^-1001`); they are printed
    -- positionally, and the output is at most 1000 longer than the source text.
    else if e > 0 || (e < -30 && -(maxExponent : Int) ≤ e) then intDec m ++ "e" ++ intDec e
    else
      let neg := m < 0
      let ds := natDec m.natAbs
      let k := (-e).toNat
      let ds := if ds.utf8ByteSize ≤ k then "".pushn '0' (k + 1 - ds.utf8ByteSize) ++ ds else ds
      -- the digits are ASCII: the byte index equals the character index
      let cut := ds.utf8ByteSize - k
      let ip := String.Pos.Raw.extract ds ⟨0⟩ ⟨cut⟩
      let fp := String.Pos.Raw.extract ds ⟨cut⟩ ⟨ds.utf8ByteSize⟩
      (if neg then "-" else "") ++ ip ++ "." ++ fp
  | .str s => "\"" ++ escape s ++ "\""
  | .arr [] => "[]"
  | .arr xs =>
    if xs.all (fun | .str _ => true | _ => false) then
      "[" ++ ", ".intercalate (xs.map (render ind)) ++ "]"
    else
      let ind' := ind ++ "  "
      "[\n" ++ ",\n".intercalate (xs.map fun x => ind' ++ render ind' x) ++ "\n" ++ ind ++ "]"
  | .obj [] => "{}"
  | .obj kvs =>
    let ind' := ind ++ "  "
    "{\n" ++ ",\n".intercalate (kvs.map fun (k, v) =>
      ind' ++ "\"" ++ escape k ++ "\": " ++ render ind' v) ++ "\n" ++ ind ++ "}"

def print (j : J) : String := render "" j

/-- Single-line output (for the engine protocol). -/
partial def renderCompact : J → String
  | .obj kvs => "{" ++ ",".intercalate (kvs.map fun (k, v) =>
      "\"" ++ escape k ++ "\":" ++ renderCompact v) ++ "}"
  | .arr xs => "[" ++ ",".intercalate (xs.map renderCompact) ++ "]"
  | j => render "" j

end Figram.Format

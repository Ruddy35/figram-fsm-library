import Figram.Format

/-!
# Текст JSON ↔ модель `J`

Строгий парсер по RFC 8259 с двумя дополнительными правилами:

* повторяющийся ключ в объекте — ошибка (RFC это разрешает, формат — нет:
  иначе два читателя могли бы понять документ по-разному);
* показатель степени числа по модулю не больше `maxExponent` — защита от
  входов вроде `1e1000000000`, которые иначе требуют огромных вычислений.

Числа в остальном любые по грамматике RFC 8259: отрицательные, дробные,
с показателем. Модель числа точная: `m · 10^e`. Ограничения полей формата
(задержки таймеров — целые от 0 до 2^53 − 1) проверяет декодер и `check`.

Разбор идёт по байтам UTF-8 самой строки (позиция — номер байта), без
промежуточного списка символов: память — сам текст и построенное значение.
Повтор ключа ищется по множеству ключей объекта (в среднем O(1) на ключ),
цифры числа переводятся в `Nat` делением пополам (не цифра за цифрой), и
числа печатаются так же — время почти линейно по длине записи.

Печать сохраняет порядок ключей модели; отступ — два пробела. Числа с
большим отрицательным показателем печатаются в научной записи, если модуль
показателя не больше `maxExponent`, иначе позиционно: всё напечатанное
читается `parse` обратно в то же значение модели.
Этот слой не покрыт доказательствами, он намеренно маленький.
-/

namespace Figram.Format

abbrev P := Except String

/-- Наибольший допустимый модуль показателя степени в тексте числа. -/
def maxExponent : Nat := 1000

/-- Наибольшая глубина вложенности массивов и объектов. Ограничивает рекурсию
парсера, печати и проверок одинаково в любом окружении (поток Go, Python, CLI). -/
def maxDepth : Nat := 256

/-! ## Разбор: позиция — номер байта UTF-8 в исходной строке -/

/-- Байт текста в позиции `i`; за концом текста — 0 (там, где это важно,
вызывающий сначала проверяет `i < s.utf8ByteSize`). -/
@[inline] def byteAt (s : String) (i : Nat) : UInt8 :=
  if h : i < s.utf8ByteSize then s.getUTF8Byte ⟨i⟩ h else 0

/-- В позиции `i` есть байт `b`. -/
@[inline] def byteIs (s : String) (i : Nat) (b : UInt8) : Bool :=
  i < s.utf8ByteSize && byteAt s i == b

@[inline] def isDigitB (b : UInt8) : Bool := 48 ≤ b && b ≤ 57

/-- В позиции `i` есть десятичная цифра. -/
@[inline] def digitAt (s : String) (i : Nat) : Bool :=
  i < s.utf8ByteSize && isDigitB (byteAt s i)

/-- Символ, начинающийся в позиции `i` (для текста ошибки). -/
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

/-- В тексте с позиции `i` (граница символа) есть хотя бы `k` символов. -/
partial def hasChars (s : String) (i k : Nat) : Bool :=
  if k = 0 then true
  else if i < s.utf8ByteSize then hasChars s (String.Pos.Raw.next s ⟨i⟩).byteIdx (k - 1)
  else false

/-- Четыре шестнадцатеричные цифры после `\u`. Если до конца текста меньше
четырёх символов — «обрыв», иначе любой не-шестнадцатеричный — «неверная». -/
def hex4 (s : String) (i : Nat) : P Nat :=
  if !hasChars s i 4 then .error "обрыв последовательности \\u"
  else
    match hexValB (byteAt s i), hexValB (byteAt s (i + 1)), hexValB (byteAt s (i + 2)),
      hexValB (byteAt s (i + 3)) with
    | some a, some b, some c, some d => .ok (((a * 16 + b) * 16 + c) * 16 + d)
    | _, _, _, _ => .error "неверная последовательность \\u"

/-- Конец участка строки без кавычки, обратной косой черты и управляющих символов. -/
partial def plainEnd (s : String) (i : Nat) : Nat :=
  if h : i < s.utf8ByteSize then
    let b := s.getUTF8Byte ⟨i⟩ h
    if b == 34 || b == 92 || b < 32 then i else plainEnd s (i + 1)
  else i

/-- Строка после открывающей кавычки: значение и позиция после закрывающей.
Участки без escape-последовательностей копируются целиком. -/
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
      -- обратная косая черта последним символом текста
      else throw "незакрытая строка"
    else throw "управляющий символ в строке"
  else throw "незакрытая строка"

/-- Конец участка цифр, начиная с `i`. -/
partial def digitsEnd (s : String) (i : Nat) : Nat :=
  if digitAt s i then digitsEnd s (i + 1) else i

/-- Значение не больше 18 цифр подряд. -/
partial def digitsSmall (s : String) (i b acc : Nat) : Nat :=
  if i < b then digitsSmall s (i + 1) b (acc * 10 + (byteAt s i - 48).toNat) else acc

/-- Значение цифр в байтах `[a, b)`: делением пополам, поэтому время — как у
умножения больших чисел, а не квадрат числа цифр. -/
partial def digitsVal (s : String) (a b : Nat) : Nat :=
  if b - a ≤ 18 then digitsSmall s a b 0
  else
    let k := (b - a) / 2
    digitsVal s a (b - k) * 10 ^ k + digitsVal s (b - k) b

/-- Значение показателя степени, насыщенное на `maxExponent + 1`: больше этого
отличать незачем, а ведущие нули любой длины не меняют значение. -/
partial def expVal (s : String) (i b acc : Nat) : Nat :=
  if i < b then expVal s (i + 1) b (min (acc * 10 + (byteAt s i - 48).toNat) (maxExponent + 1))
  else acc

/-- Число JSON по грамматике RFC 8259 с позиции `i` → точное `m · 10^e` и
позиция после числа. -/
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

/-- Множество ключей объекта для проверки повтора: корзины по хешу. -/
structure KeySet where
  count : Nat
  buckets : Array (List String)

/-- Пустое множество без корзин: у малых объектов (до 8 ключей) повтор ищется
линейно по уже прочитанным ключам, множество не строится. -/
def KeySet.none : KeySet := ⟨0, #[]⟩

@[inline] def KeySet.slot (n : Nat) (k : String) : Nat := (hash k % n.toUInt64).toNat

def KeySet.grow (ks : KeySet) : KeySet :=
  let n := ks.buckets.size * 2
  ⟨ks.count, ks.buckets.foldl (fun acc l => l.foldl (fun acc k => acc.modify (KeySet.slot n k) (k :: ·)) acc)
    (Array.replicate n [])⟩

/-- `(true, ks)`, если ключ уже есть; иначе `(false, ks ∪ {k})`. Корзин не меньше одной. -/
def KeySet.insert (ks : KeySet) (k : String) : Bool × KeySet :=
  let i := KeySet.slot ks.buckets.size k
  if (ks.buckets[i]!).contains k then (true, ks)
  else
    let ks : KeySet := ⟨ks.count + 1, ks.buckets.modify i (k :: ·)⟩
    (false, if ks.count * 4 > ks.buckets.size * 3 then ks.grow else ks)

/-- Повтор ключа `k` среди прочитанных пар `acc`: до 8 ключей — линейный поиск,
дальше — множество `keys` (строится один раз из прочитанных ключей). -/
def dupKey (acc : Array (String × J)) (keys : KeySet) (k : String) : Bool × KeySet :=
  if acc.size < 8 then (acc.any (·.1 == k), keys)
  else
    let keys := if keys.buckets.isEmpty then
      acc.foldl (fun ks p => (ks.insert p.1).2) ⟨0, Array.replicate 32 []⟩ else keys
    keys.insert k

mutual
/-- Значение JSON с позиции `i` (пробелы перед ним пропускаются) и позиция после него. -/
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

/-! ## Печать -/

/-- Десятичная запись натурального числа. Большие числа делятся пополам по
степени десяти (`Nat.repr` для них переводит цифру за цифрой, время — квадрат
длины записи). -/
partial def natDec (n : Nat) : String :=
  if n < 1000000000000000000 then Nat.repr n
  else
    -- k ≈ половина числа цифр; 10^k ≤ √n, поэтому старшая часть не ноль
    let k := n.log2 * 3 / 20
    let p := 10 ^ k
    let lo := natDec (n % p)
    natDec (n / p) ++ "".pushn '0' (k - lo.utf8ByteSize) ++ lo

/-- Десятичная запись целого, как `toString` для `Int`. -/
def intDec : Int → String
  | .ofNat n => natDec n
  | .negSucc n => "-" ++ natDec (n + 1)

def hexDigit (n : Nat) : Char :=
  if n < 10 then Char.ofNat ('0'.toNat + n) else Char.ofNat ('a'.toNat + n - 10)

/-- `\u00XX` для кода меньше 256. -/
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

/-- Текст для диагностики в терминал и журнал: управляющие символы (C0, DEL, C1)
заменяются на `\u00XX`, поэтому имя из документа не может начать новую строку
или послать терминалу escape-последовательность. -/
def sanitize (s : String) : String :=
  s.foldl (fun acc c =>
    if c.toNat < 0x20 || (0x7f ≤ c.toNat && c.toNat < 0xa0) then u00 acc c.toNat
    else acc.push c) ""

partial def render (ind : String) : J → String
  | .null => "null"
  | .bool b => if b then "true" else "false"
  | .num m e =>
    if e = 0 then intDec m
    -- Показатель в научной записи не больше `maxExponent` по модулю: иначе
    -- `parse` не прочитал бы напечатанное. Такие `e` бывают только у чисел
    -- с дробной частью (`1.0e-1000` → `10 · 10^-1001`); они печатаются
    -- позиционно, длина записи не больше длины исходного текста плюс 1000.
    else if e > 0 || (e < -30 && -(maxExponent : Int) ≤ e) then intDec m ++ "e" ++ intDec e
    else
      let neg := m < 0
      let ds := natDec m.natAbs
      let k := (-e).toNat
      let ds := if ds.utf8ByteSize ≤ k then "".pushn '0' (k + 1 - ds.utf8ByteSize) ++ ds else ds
      -- цифры — ASCII: номер байта совпадает с номером символа
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

/-- Запись в одну строку (для протокола движка). -/
partial def renderCompact : J → String
  | .obj kvs => "{" ++ ",".intercalate (kvs.map fun (k, v) =>
      "\"" ++ escape k ++ "\":" ++ renderCompact v) ++ "}"
  | .arr xs => "[" ++ ",".intercalate (xs.map renderCompact) ++ "]"
  | j => render "" j

end Figram.Format

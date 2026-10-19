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

partial def skipWs : List Char → List Char
  | c :: cs => if c = ' ' || c = '\n' || c = '\r' || c = '\t' then skipWs cs else c :: cs
  | [] => []

def hexVal (c : Char) : Option Nat :=
  if '0' ≤ c && c ≤ '9' then some (c.toNat - '0'.toNat)
  else if 'a' ≤ c && c ≤ 'f' then some (c.toNat - 'a'.toNat + 10)
  else if 'A' ≤ c && c ≤ 'F' then some (c.toNat - 'A'.toNat + 10)
  else none

def hex4 : List Char → P (Nat × List Char)
  | a :: b :: c :: d :: rest =>
    match hexVal a, hexVal b, hexVal c, hexVal d with
    | some a, some b, some c, some d => .ok (((a * 16 + b) * 16 + c) * 16 + d, rest)
    | _, _, _, _ => .error "неверная последовательность \\u"
  | _ => .error "обрыв последовательности \\u"

/-- Строка после открывающей кавычки. -/
partial def parseStr (acc : List Char) : List Char → P (String × List Char)
  | [] => .error "незакрытая строка"
  | '"' :: rest => .ok (String.ofList acc.reverse, rest)
  | '\\' :: e :: rest =>
    match e with
    | '"' => parseStr ('"' :: acc) rest
    | '\\' => parseStr ('\\' :: acc) rest
    | '/' => parseStr ('/' :: acc) rest
    | 'b' => parseStr ('\x08' :: acc) rest
    | 'f' => parseStr ('\x0c' :: acc) rest
    | 'n' => parseStr ('\n' :: acc) rest
    | 'r' => parseStr ('\r' :: acc) rest
    | 't' => parseStr ('\t' :: acc) rest
    | 'u' => do
      let (hi, rest) ← hex4 rest
      if 0xD800 ≤ hi && hi < 0xDC00 then
        match rest with
        | '\\' :: 'u' :: rest' =>
          let (lo, rest'') ← hex4 rest'
          if 0xDC00 ≤ lo && lo < 0xE000 then
            parseStr (Char.ofNat (0x10000 + (hi - 0xD800) * 0x400 + (lo - 0xDC00)) :: acc) rest''
          else throw "непарный суррогат в \\u"
        | _ => throw "непарный суррогат в \\u"
      else if 0xDC00 ≤ hi && hi < 0xE000 then throw "непарный суррогат в \\u"
      else parseStr (Char.ofNat hi :: acc) rest
    | _ => .error s!"неизвестная escape-последовательность \\{e}"
  | c :: rest =>
    if c.toNat < 0x20 then .error "управляющий символ в строке" else parseStr (c :: acc) rest

/-- Цифры подряд: значение и количество. -/
partial def parseDigits (acc : Nat) (cnt : Nat) : List Char → Nat × Nat × List Char
  | c :: rest => if '0' ≤ c && c ≤ '9' then parseDigits (acc * 10 + (c.toNat - '0'.toNat)) (cnt + 1) rest
                 else (acc, cnt, c :: rest)
  | [] => (acc, cnt, [])

/-- Число JSON по грамматике RFC 8259 → точное `m · 10^e`. -/
def parseNum (cs : List Char) : P (Int × Int × List Char) := do
  let (neg, cs) := match cs with
    | '-' :: rest => (true, rest)
    | _ => (false, cs)
  let (ip, ic, cs) := parseDigits 0 0 cs
  if ic = 0 then throw "ожидалась цифра"
  let (fp, fc, cs) ← match cs with
    | '.' :: rest =>
      let (f, n, r) := parseDigits 0 0 rest
      if n = 0 then throw "после «.» нужна цифра" else pure (f, n, r)
    | _ => pure (0, 0, cs)
  let (ex, cs) ← match cs with
    | c :: rest =>
      if c = 'e' || c = 'E' then
        let (eneg, rest) := match rest with
          | '-' :: r => (true, r)
          | '+' :: r => (false, r)
          | r => (false, r)
        let (ev, en, r) := parseDigits 0 0 rest
        if en = 0 then throw "в показателе нужна цифра"
        if ev > maxExponent then throw s!"показатель степени по модулю больше {maxExponent}"
        pure ((if eneg then -(ev : Int) else ev), r)
      else pure (0, c :: rest)
    | [] => pure (0, [])
  let m : Int := ip * 10 ^ fc + fp
  pure ((if neg then -m else m), ex - fc, cs)

mutual
partial def parseValue (depth : Nat) (cs : List Char) : P (J × List Char) := do
  match skipWs cs with
  | '{' :: rest =>
    if depth ≥ maxDepth then throw s!"вложенность больше {maxDepth}"
    parseObj (depth + 1) [] (skipWs rest) true
  | '[' :: rest =>
    if depth ≥ maxDepth then throw s!"вложенность больше {maxDepth}"
    parseArr (depth + 1) [] (skipWs rest) true
  | '"' :: rest => let (s, r) ← parseStr [] rest; pure (.str s, r)
  | 't' :: 'r' :: 'u' :: 'e' :: rest => pure (.bool true, rest)
  | 'f' :: 'a' :: 'l' :: 's' :: 'e' :: rest => pure (.bool false, rest)
  | 'n' :: 'u' :: 'l' :: 'l' :: rest => pure (.null, rest)
  | c :: rest =>
    if ('0' ≤ c && c ≤ '9') || c = '-' then
      if c = '0' then
        match rest with
        | d :: _ => if '0' ≤ d && d ≤ '9' then throw "ведущие нули запрещены" else pure ()
        | [] => pure ()
      if c = '-' then
        match rest with
        | '0' :: d :: _ => if '0' ≤ d && d ≤ '9' then throw "ведущие нули запрещены" else pure ()
        | _ => pure ()
      let (m, e, r) ← parseNum (c :: rest); pure (.num m e, r)
    else throw s!"неожиданный символ «{c}»"
  | [] => throw "неожиданный конец текста"
partial def parseArr (depth : Nat) (acc : List J) (cs : List Char) (first : Bool) : P (J × List Char) := do
  match cs with
  | ']' :: rest =>
    if first then pure (.arr [], rest) else throw "лишняя запятая перед «]»"
  | _ =>
    let (v, rest) ← parseValue depth cs
    match skipWs rest with
    | ',' :: rest => parseArr depth (v :: acc) (skipWs rest) false
    | ']' :: rest => pure (.arr (v :: acc).reverse, rest)
    | _ => throw "ожидалась «,» или «]»"
partial def parseObj (depth : Nat) (acc : List (String × J)) (cs : List Char) (first : Bool) :
    P (J × List Char) := do
  match cs with
  | '}' :: rest =>
    if first then pure (.obj [], rest) else throw "лишняя запятая перед «}»"
  | '"' :: rest =>
    let (k, rest) ← parseStr [] rest
    if acc.any (·.1 == k) then throw s!"повторяющийся ключ «{k}»"
    match skipWs rest with
    | ':' :: rest =>
      let (v, rest) ← parseValue depth rest
      match skipWs rest with
      | ',' :: rest => parseObj depth ((k, v) :: acc) (skipWs rest) false
      | '}' :: rest => pure (.obj ((k, v) :: acc).reverse, rest)
      | _ => throw "ожидалась «,» или «}»"
    | _ => throw "ожидалось «:»"
  | _ => throw "ожидался ключ в кавычках"
end

def parse (s : String) : P J := do
  let (v, rest) ← parseValue 0 s.toList
  match skipWs rest with
  | [] => pure v
  | _ => throw "лишний текст после JSON"

def hexDigit (n : Nat) : Char :=
  if n < 10 then Char.ofNat ('0'.toNat + n) else Char.ofNat ('a'.toNat + n - 10)

def escape (s : String) : String :=
  s.foldl (fun acc c =>
    acc ++ match c with
    | '"' => "\\\""
    | '\\' => "\\\\"
    | '\n' => "\\n"
    | '\r' => "\\r"
    | '\t' => "\\t"
    | c => if c.toNat < 0x20 then
             "\\u00" ++ String.ofList [hexDigit (c.toNat / 16), hexDigit (c.toNat % 16)]
           else String.singleton c) ""

/-- Текст для диагностики в терминал и журнал: управляющие символы (C0, DEL, C1)
заменяются на `\u00XX`, поэтому имя из документа не может начать новую строку
или послать терминалу escape-последовательность. -/
def sanitize (s : String) : String :=
  s.foldl (fun acc c =>
    acc ++ if c.toNat < 0x20 || (0x7f ≤ c.toNat && c.toNat < 0xa0) then
      "\\u00" ++ String.ofList [hexDigit (c.toNat / 16), hexDigit (c.toNat % 16)]
    else String.singleton c) ""

partial def render (ind : String) : J → String
  | .null => "null"
  | .bool b => if b then "true" else "false"
  | .num m e =>
    if e = 0 then toString m
    -- Показатель в научной записи не больше `maxExponent` по модулю: иначе
    -- `parse` не прочитал бы напечатанное. Такие `e` бывают только у чисел
    -- с дробной частью (`1.0e-1000` → `10 · 10^-1001`); они печатаются
    -- позиционно, длина записи не больше длины исходного текста плюс 1000.
    else if e > 0 || (e < -30 && -(maxExponent : Int) ≤ e) then s!"{m}e{e}"
    else
      let neg := m < 0
      let ds := toString m.natAbs
      let k := (-e).toNat
      let ds := if ds.length ≤ k then String.ofList (List.replicate (k + 1 - ds.length) '0') ++ ds else ds
      let ip := String.ofList (ds.toList.take (ds.length - k))
      let fp := String.ofList (ds.toList.drop (ds.length - k))
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

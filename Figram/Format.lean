import Figram.Lint

/-!
# Document format

The mathematical model of JSON (`J`), the document types, the encoder `encode`
and the decoder `decode`. The derivation of the format and its properties are in
docs/format.md, the proofs in `Figram.FormatProofs`.

A JSON object is modelled as a list of key-value pairs in the order written.
Duplicate keys are rejected by the text parser (`Figram.FormatIO`) and never
reach this model.
-/

namespace Figram.Format

/-- A JSON value. A number is the exact decimal `m · 10^e`, so the model
loses no number that may occur in extensions. -/
inductive J where
  | null
  | bool (b : Bool)
  | num (m : Int) (e : Int)
  | str (s : String)
  | arr (xs : List J)
  | obj (kvs : List (String × J))
  deriving Repr, Inhabited

abbrev Obj := List (String × J)

/-- The value of a key: the first occurrence. -/
def get (kvs : Obj) (k : String) : Option J :=
  (kvs.find? (fun p => decide (p.1 = k))).map (·.2)

/-- The keys the format does not know: they are kept as they are. -/
def unknown (known : List String) (kvs : Obj) : Obj :=
  kvs.filter (fun p => decide (p.1 ∉ known))

/-- The extra keys do not coincide with the format keys. -/
def extOK (known : List String) (ext : Obj) : Prop := ∀ p ∈ ext, p.1 ∉ known

/-- A field that is written only when it differs from the default value. -/
def field (k : String) (present : Bool) (j : J) : Obj := if present then [(k, j)] else []

/-! ## Format keys of version 1 -/

def docKeys : List String := ["figram", "requires", "conditions", "calls", "variables", "bot"]
def nodeKeys : List String := ["initial", "children", "on", "timers"]
def variantKeys : List String := ["if", "to", "resume", "do"]

/-- The semantic extensions this reader understands. Version 1 has none. -/
def knownFeatures : List String := []

/-! ## Document types -/

/-- A variant of an arrow. -/
structure Variant where
  /-- The name of a condition from `conditions`; none means "otherwise / always". -/
  cond : Option String
  /-- Where to lead: a message or a section. -/
  to : NodeId
  /-- `true` means "resume section `to` where the user left off". -/
  resume : Bool
  /-- The names of actions from `calls`, in order. -/
  calls : List String
  ext : Obj
  deriving Repr

/-- A node: a message or a section. A section is a node with `children`. -/
inductive NodeDoc where
  | message (on : List (EventId × List Variant)) (timers : List (String × Nat)) (ext : Obj)
  | sec (initial : NodeId) (children : List (NodeId × NodeDoc))
      (on : List (EventId × List Variant)) (ext : Obj)
  deriving Repr

structure Doc where
  requires : List String
  /-- Conditions: name → the text the designer wrote. -/
  conditions : List (String × String)
  /-- Actions: name → description. -/
  calls : List (String × String)
  /-- Variables: name → description. -/
  variables : List (String × String)
  /-- The whole bot is the root section. -/
  bot : NodeDoc
  ext : Obj
  deriving Repr

/-! ## Encoding -/

def encStrMap (m : List (String × String)) : Obj := m.map fun p => (p.1, .str p.2)
def encNatMap (m : List (String × Nat)) : Obj := m.map fun p => (p.1, .num p.2 0)

def encVariant (v : Variant) : J :=
  .obj (field "if" v.cond.isSome (.str (v.cond.getD ""))
    ++ [("to", .str v.to)]
    ++ field "resume" v.resume (.bool true)
    ++ field "do" (!v.calls.isEmpty) (.arr (v.calls.map .str))
    ++ v.ext)

def encOn (on : List (EventId × List Variant)) : Obj :=
  on.map fun p => (p.1, .arr (p.2.map encVariant))

mutual
def encNode : NodeDoc → J
  | .message on ts ext =>
    .obj (field "on" (!on.isEmpty) (.obj (encOn on))
      ++ field "timers" (!ts.isEmpty) (.obj (encNatMap ts))
      ++ ext)
  | .sec i cs on ext =>
    .obj ([("initial", .str i)]
      ++ field "on" (!on.isEmpty) (.obj (encOn on))
      ++ [("children", .obj (encChildren cs))]
      ++ ext)
def encChildren : List (NodeId × NodeDoc) → Obj
  | [] => []
  | (k, n) :: rest => (k, encNode n) :: encChildren rest
end

def encode (d : Doc) : J :=
  .obj ([("figram", .num 1 0)]
    ++ field "requires" (!d.requires.isEmpty) (.arr (d.requires.map .str))
    ++ field "conditions" (!d.conditions.isEmpty) (.obj (encStrMap d.conditions))
    ++ field "calls" (!d.calls.isEmpty) (.obj (encStrMap d.calls))
    ++ field "variables" (!d.variables.isEmpty) (.obj (encStrMap d.variables))
    ++ [("bot", encNode d.bot)]
    ++ d.ext)

/-! ## Decoding -/

abbrev R := Except String

/-- The largest integer in format fields: 2^53 − 1. JavaScript (the editor), Go, Rust
and Python represent it exactly. -/
def maxInt : Nat := 9007199254740991

/-- The value of a number as a natural number, if it is a non-negative integer
(mathematically: `7200000`, `7.2e6` and `7200000.0` are the same number).
Huge powers are not computed: such a number is certainly larger than `maxInt`. -/
def numToNat (m e : Int) : Option Nat :=
  if m < 0 then none
  else if m = 0 then some 0
  else if 0 ≤ e then (if e ≤ 18 then some (m.toNat * 10 ^ e.toNat) else none)
  else
    let k := (-e).toNat
    if k > (toString m.toNat).length then none
    else if m.toNat % 10 ^ k = 0 then some (m.toNat / 10 ^ k) else none

def decStrs : List J → R (List String)
  | [] => .ok []
  | .str s :: rest => (decStrs rest).map (s :: ·)
  | _ :: _ => .error "expected a list of strings"

def decStrMap : Obj → R (List (String × String))
  | [] => .ok []
  | (k, .str s) :: rest => (decStrMap rest).map ((k, s) :: ·)
  | (k, _) :: _ => .error s!"`{k}`: expected a string"

def decNatMap : Obj → R (List (String × Nat))
  | [] => .ok []
  | (k, .num m e) :: rest =>
    match numToNat m e with
    | some n => (decNatMap rest).map ((k, n) :: ·)
    | none => .error s!"`{k}`: expected a non-negative integer at most {maxInt}"
  | (k, _) :: _ => .error s!"`{k}`: expected a non-negative integer"

def decVariant : J → R Variant
  | .obj kvs => do
    let cond ← match get kvs "if" with
      | none => pure none
      | some (.str c) => pure (some c)
      | some _ => throw "`if`: expected a string"
    let to ← match get kvs "to" with
      | some (.str t) => pure t
      | _ => throw "variant: missing field `to`"
    let resume ← match get kvs "resume" with
      | none => pure false
      | some (.bool b) => pure b
      | some _ => throw "`resume`: expected true or false"
    let calls ← match get kvs "do" with
      | none => pure []
      | some (.arr xs) => decStrs xs
      | some _ => throw "`do`: expected a list"
    pure ⟨cond, to, resume, calls, unknown variantKeys kvs⟩
  | _ => .error "variant must be an object"

def decVariants : List J → R (List Variant)
  | [] => .ok []
  | j :: rest => do pure ((← decVariant j) :: (← decVariants rest))

def decOn : Obj → R (List (EventId × List Variant))
  | [] => .ok []
  | (e, .arr xs) :: rest => do pure ((e, ← decVariants xs) :: (← decOn rest))
  | (e, _) :: _ => .error s!"`{e}`: expected a list of variants"

def decOnField (kvs : Obj) : R (List (EventId × List Variant)) :=
  match get kvs "on" with
  | none => pure []
  | some (.obj es) => decOn es
  | some _ => throw "`on` must be an object"

mutual
def decNode : J → R NodeDoc
  | .obj kvs => do
    let on ← decOnField kvs
    match (← decChildrenIn kvs) with
    | none =>
      if (get kvs "initial").isSome then throw "`initial` without `children`"
      let ts ← match get kvs "timers" with
        | none => pure []
        | some (.obj ts) => decNatMap ts
        | some _ => throw "`timers` must be an object"
      pure (.message on ts (unknown nodeKeys kvs))
    | some cs =>
      if (get kvs "timers").isSome then throw "section: `timers` is not allowed"
      let i ← match get kvs "initial" with
        | some (.str i) => pure i
        | _ => throw "section: missing field `initial`"
      pure (.sec i cs on (unknown nodeKeys kvs))
  | _ => .error "node must be an object"
/-- The first `children` field (if any), decoded. -/
def decChildrenIn : Obj → R (Option (List (NodeId × NodeDoc)))
  | [] => .ok none
  | (k, v) :: rest =>
    if k = "children" then
      match v with
      | .obj cs => (decChildren cs).map some
      | _ => .error "`children` must be an object"
    else decChildrenIn rest
def decChildren : Obj → R (List (NodeId × NodeDoc))
  | [] => .ok []
  | (k, j) :: rest => do pure ((k, ← decNode j) :: (← decChildren rest))
end

def decDecl (kvs : Obj) (k : String) : R (List (String × String)) :=
  match get kvs k with
  | none => pure []
  | some (.obj m) => decStrMap m
  | some _ => throw s!"`{k}` must be an object"

/-- Decoding of the structure without the `requires` check. -/
def decodeRaw : J → R Doc
  | .obj kvs => do
    match get kvs "figram" with
    | some (.num m e) => if numToNat m e = some 1 then pure () else throw "field `figram` must be 1"
    | _ => throw "field `figram` must be 1"
    let req ← match get kvs "requires" with
      | none => pure []
      | some (.arr xs) => decStrs xs
      | some _ => throw "`requires`: expected a list"
    let conds ← decDecl kvs "conditions"
    let calls ← decDecl kvs "calls"
    let vars ← decDecl kvs "variables"
    let bot ← match get kvs "bot" with
      | some j => decNode j
      | none => throw "missing field `bot`"
    match bot with
    | .sec .. => pure ⟨req, conds, calls, vars, bot, unknown docKeys kvs⟩
    | .message .. => throw "`bot` must be a section"
  | _ => .error "document must be an object"

/-- **Reading a document.** A document that requires unknown extensions
is rejected as a whole instead of being read partially. -/
def decode (j : J) : R Doc := do
  let d ← decodeRaw j
  if d.requires.all (fun f => decide (f ∈ knownFeatures)) then pure d
  else throw "document requires extensions this reader does not know"

/-! ## The meaning of a document: the chart `Chart` -/

/-- The identifier of the root section. The check forbids an empty node key. -/
def rootId : NodeId := ""

def Variant.toBranch (v : Variant) : Branch :=
  ⟨v.cond, if v.resume then .history v.to else .node v.to, v.calls.map .call⟩

def onTrans (id : NodeId) (on : List (EventId × List Variant)) : List Transition :=
  on.map fun p => ⟨id, p.1, p.2.map Variant.toBranch⟩

abbrev Flat := List Node × List Transition × List Timer

def Flat.app (a b : Flat) : Flat := (a.1 ++ b.1, a.2.1 ++ b.2.1, a.2.2 ++ b.2.2)

mutual
def flatNode (id : NodeId) (parent : Option NodeId) : NodeDoc → Flat
  | .message on ts _ =>
    ([⟨id, parent, .message⟩], onTrans id on, ts.map fun p => ⟨p.1, id, p.2⟩)
  | .sec i cs on _ =>
    Flat.app ([⟨id, parent, .sec i⟩], onTrans id on, []) (flatChildren id cs)
def flatChildren (parent : NodeId) : List (NodeId × NodeDoc) → Flat
  | [] => ([], [], [])
  | (k, n) :: rest => Flat.app (flatNode k (some parent) n) (flatChildren parent rest)
end

def Doc.toChart (d : Doc) : Chart :=
  let f := flatNode rootId none d.bot
  ⟨f.1, rootId, f.2.1, f.2.2⟩

/-! ## Checking a document

The semantic conditions that the JSON structure does not express, plus the conditions
without which a document cannot be written as text and read back. -/

def dups (l : List String) : List String :=
  dedup (l.filter fun k => (l.filter (· == k)).length > 1)

/-- The keys of all objects inside a value are unique. -/
partial def J.keysUnique : J → Bool
  | .obj kvs => (dups (kvs.map (·.1))).isEmpty && kvs.all (fun p => J.keysUnique p.2)
  | .arr xs => xs.all J.keysUnique
  | _ => true

/-- The check of the extra keys of one object. -/
def extErrors (whereStr : String) (known : List String) (ext : Obj) : List String :=
  (ext.filterMap fun p =>
    if p.1 ∈ known then some s!"{whereStr}: extra key `{p.1}` collides with a format key"
    else if !(J.keysUnique p.2) then some s!"{whereStr}: duplicate keys in the value of `{p.1}`"
    else none)
  ++ (dups (ext.map (·.1))).map fun k => s!"{whereStr}: duplicate key `{k}`"

def mapErrors (whereStr : String) (keys : List String) : List String :=
  (if keys.contains "" then [s!"{whereStr}: empty name"] else [])
  ++ (dups keys).map fun k => s!"{whereStr}: duplicate name `{k}`"

def variantErrors (whereStr : String) (v : Variant) : List String :=
  (if v.to = "" then [s!"{whereStr}: empty target `to`"] else [])
  ++ (if v.cond = some "" then [s!"{whereStr}: empty condition name"] else [])
  ++ (if v.calls.contains "" then [s!"{whereStr}: empty action name"] else [])
  ++ extErrors whereStr variantKeys v.ext

def onErrors (id : NodeId) (on : List (EventId × List Variant)) : List String :=
  mapErrors s!"events of node `{id}`" (on.map (·.1))
  ++ on.flatMap fun p => p.2.flatMap (variantErrors s!"`{id}` → `{p.1}`")

partial def nodeErrors (id : NodeId) : NodeDoc → List String
  | .message on ts ext =>
    onErrors id on
    ++ mapErrors s!"timers of message `{id}`" (ts.map (·.1))
    ++ (ts.filterMap fun p => if p.2 > maxInt then
          some s!"timer `{p.1}` in `{id}`: delay exceeds {maxInt} ms" else none)
    ++ extErrors s!"node `{id}`" nodeKeys ext
  | .sec _ cs on ext =>
    onErrors id on
    ++ mapErrors s!"children of section `{id}`" (cs.map (·.1))
    ++ extErrors s!"node `{id}`" nodeKeys ext
    ++ cs.flatMap fun p => nodeErrors p.1 p.2

partial def allTimers : NodeDoc → List String
  | .message _ ts _ => ts.map (·.1)
  | .sec _ cs _ _ => cs.flatMap fun p => allTimers p.2

partial def allEvents : NodeDoc → List String
  | .message on _ _ => on.map (·.1)
  | .sec _ cs on _ => on.map (·.1) ++ cs.flatMap fun p => allEvents p.2

/-! ### The hypotheses of the write-and-read theorem in computable form -/

def extOKb (known : List String) (ext : Obj) : Bool := ext.all (fun p => decide (p.1 ∉ known))

def onOKb (on : List (EventId × List Variant)) : Bool :=
  on.all (fun p => p.2.all (fun v => extOKb variantKeys v.ext))

mutual
def nodeOKb : NodeDoc → Bool
  | .message on _ ext => onOKb on && extOKb nodeKeys ext
  | .sec _ cs on ext => childrenOKb cs && onOKb on && extOKb nodeKeys ext
def childrenOKb : List (NodeId × NodeDoc) → Bool
  | [] => true
  | (_, n) :: rest => nodeOKb n && childrenOKb rest
end

def Doc.wfOK (d : Doc) : Bool :=
  d.requires.all (fun f => decide (f ∈ knownFeatures)) && nodeOKb d.bot
  && (match d.bot with | .sec .. => true | .message .. => false)
  && extOKb docKeys d.ext

def Doc.check (d : Doc) : List String :=
  let c := d.toChart
  let branches := c.transitions.flatMap (·.branches)
  let undeclaredCond := (branches.filterMap (·.guard)).filterMap fun n =>
    if n = "" || d.conditions.any (·.1 == n) then none
    else some s!"condition `{n}` is not described in `conditions`"
  let undeclaredCall := (branches.flatMap (·.actions)).filterMap fun
    | .call n => if n = "" || d.calls.any (·.1 == n) then none
                 else some s!"action `{n}` is not described in `calls`"
    | _ => none
  let timers := allTimers d.bot
  let badTimerEvents := (allEvents d.bot).filterMap fun e =>
    if e.startsWith "timer:" && !(timers.contains (e.drop 6).toString) then
      some s!"event `{e}`: no such timer (the prefix `timer:` is reserved)"
    else none
  let structural :=
    (if d.requires.all (· ∈ knownFeatures) then [] else ["document requires extensions this reader does not know"])
    ++ (match d.bot with | .sec .. => [] | .message .. => ["`bot` must be a section"])
    ++ mapErrors "conditions" (d.conditions.map (·.1))
    ++ mapErrors "calls" (d.calls.map (·.1))
    ++ mapErrors "variables" (d.variables.map (·.1))
    ++ extErrors "document" docKeys d.ext
    ++ nodeErrors "bot" d.bot
  (if d.wfOK then [] else ["document violates the format rules (see the errors below)"])
  ++ structural ++ dedup undeclaredCond ++ dedup undeclaredCall ++ dedup badTimerEvents ++ c.lint

end Figram.Format

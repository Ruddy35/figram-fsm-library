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
def variantKeys : List String := ["if", "to", "resume", "back", "do"]
def callKeys : List String := ["text", "detached", "outcomes", "every"]

/-- The semantic extensions this reader understands. Version 1 has none. -/
def knownFeatures : List String := []

/-! ## Document types -/

/-- A variant of an arrow. -/
structure Variant where
  /-- The name of a condition from `conditions`; none means "otherwise / always". -/
  cond : Option String
  /-- Where to lead: a message or a section; none — no move (decision 13 of docs/format.md,
  section 7). -/
  to : Option NodeId
  /-- `true` means "resume section `to` where the user left off". -/
  resume : Bool
  /-- `true` means "back to the previous section"; `to` (with `resume`) is where to lead when
  there is no place to go back to. -/
  back : Bool
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

/-- An action: its description and its properties. -/
structure CallDecl where
  text : String
  /-- A background action (decision 11). -/
  detached : Bool
  /-- The outcomes it reports, without `failed` (decision 12). -/
  outcomes : List String
  /-- Every call is answered, not only the latest. -/
  every : Bool
  ext : Obj
  deriving Repr

/-- An action with only a text, written as the text. -/
def CallDecl.plain (c : CallDecl) : Bool :=
  !c.detached && c.outcomes.isEmpty && !c.every && c.ext.isEmpty

/-- Whether the machine waits for the outcomes of the action. -/
def CallDecl.wait (c : CallDecl) : Wait :=
  if c.outcomes.isEmpty then .free else if c.every then .every else .latest

structure Doc where
  requires : List String
  /-- Conditions: name → the text the designer wrote. -/
  conditions : List (String × String)
  /-- Actions: name → description and properties. -/
  calls : List (String × CallDecl)
  /-- Variables: name → description. -/
  variables : List (String × String)
  /-- The whole bot is the root section. -/
  bot : NodeDoc
  ext : Obj
  deriving Repr

/-! ## Encoding -/

def encStrMap (m : List (String × String)) : Obj := m.map fun p => (p.1, .str p.2)
def encNatMap (m : List (String × Nat)) : Obj := m.map fun p => (p.1, .num p.2 0)

def encCall (c : CallDecl) : J :=
  if c.plain then .str c.text
  else .obj ([("text", .str c.text)]
    ++ field "detached" c.detached (.bool true)
    ++ field "outcomes" (!c.outcomes.isEmpty) (.arr (c.outcomes.map .str))
    ++ field "every" c.every (.bool true)
    ++ c.ext)

def encCalls (m : List (String × CallDecl)) : Obj := m.map fun p => (p.1, encCall p.2)

def encVariant (v : Variant) : J :=
  .obj (field "if" v.cond.isSome (.str (v.cond.getD ""))
    ++ field "to" v.to.isSome (.str (v.to.getD ""))
    ++ field "resume" v.resume (.bool true)
    ++ field "back" v.back (.bool true)
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
    ++ field "calls" (!d.calls.isEmpty) (.obj (encCalls d.calls))
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
      | none => pure none
      | some (.str t) => pure (some t)
      | some _ => throw "`to`: expected a string"
    let resume ← match get kvs "resume" with
      | none => pure false
      | some (.bool b) => pure b
      | some _ => throw "`resume`: expected true or false"
    let back ← match get kvs "back" with
      | none => pure false
      | some (.bool b) => pure b
      | some _ => throw "`back`: expected true or false"
    let calls ← match get kvs "do" with
      | none => pure []
      | some (.arr xs) => decStrs xs
      | some _ => throw "`do`: expected a list"
    pure ⟨cond, to, resume, back, calls, unknown variantKeys kvs⟩
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

def decFlag (kvs : Obj) (k : String) : R Bool :=
  match get kvs k with
  | none => pure false
  | some (.bool b) => pure b
  | some _ => throw s!"`{k}`: expected true or false"

/-- An action: a text, or an object with the text and the properties. -/
def decCall : J → R CallDecl
  | .str t => pure ⟨t, false, [], false, []⟩
  | .obj kvs => do
    let text ← match get kvs "text" with
      | some (.str t) => pure t
      | _ => throw "action: missing field `text`"
    let detached ← decFlag kvs "detached"
    let outcomes ← match get kvs "outcomes" with
      | none => pure []
      | some (.arr xs) => decStrs xs
      | some _ => throw "`outcomes`: expected a list"
    let every ← decFlag kvs "every"
    pure ⟨text, detached, outcomes, every, unknown callKeys kvs⟩
  | _ => .error "action: expected a text or an object"

def decCalls : Obj → R (List (String × CallDecl))
  | [] => .ok []
  | (k, j) :: rest => do
    let c ← (decCall j).mapError (s!"`{k}`: " ++ ·)
    pure ((k, c) :: (← decCalls rest))

def decCallsField (kvs : Obj) : R (List (String × CallDecl)) :=
  match get kvs "calls" with
  | none => pure []
  | some (.obj m) => decCalls m
  | some _ => throw "`calls` must be an object"

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
    let calls ← decCallsField kvs
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

def Variant.dest (v : Variant) : Dest :=
  match v.to with
  | none => .stay
  | some t =>
    let d := if v.resume then .history t else .node t
    if v.back then .back d else d

/-- The properties of an action that the machine sees: background, and how it waits. -/
abbrev CallOpts := String → Bool × Wait

/-- The properties of the actions of a document (an undeclared action has none). -/
def callOpts (calls : List (String × CallDecl)) (n : String) : Bool × Wait :=
  match lookup calls n with
  | some c => (c.detached, c.wait)
  | none => (false, .free)

def callAction (o : CallOpts) (n : String) : Action := .call n (o n).1 (o n).2 0

def Variant.toBranch (o : CallOpts) (v : Variant) : Branch :=
  ⟨v.cond, v.dest, v.calls.map (callAction o)⟩

def onTrans (o : CallOpts) (id : NodeId) (on : List (EventId × List Variant)) : List Transition :=
  on.map fun p => ⟨id, p.1, p.2.map (Variant.toBranch o)⟩

abbrev Flat := List Node × List Transition × List Timer

def Flat.app (a b : Flat) : Flat := (a.1 ++ b.1, a.2.1 ++ b.2.1, a.2.2 ++ b.2.2)

mutual
def flatNode (o : CallOpts) (id : NodeId) (parent : Option NodeId) : NodeDoc → Flat
  | .message on ts _ =>
    ([⟨id, parent, .message⟩], onTrans o id on, ts.map fun p => ⟨p.1, id, p.2⟩)
  | .sec i cs on _ =>
    Flat.app ([⟨id, parent, .sec i⟩], onTrans o id on, []) (flatChildren o id cs)
def flatChildren (o : CallOpts) (parent : NodeId) : List (NodeId × NodeDoc) → Flat
  | [] => ([], [], [])
  | (k, n) :: rest => Flat.app (flatNode o k (some parent) n) (flatChildren o parent rest)
end

def Doc.toChart (d : Doc) : Chart :=
  let f := flatNode (callOpts d.calls) rootId none d.bot
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

/-- The check of the extra keys of one object. An extra key must start with `x-`:
a misspelt format key (`iff`, `tiemrs`) is an error, not an annotation. -/
def extErrors (whereStr : String) (known : List String) (ext : Obj) : List String :=
  (ext.filterMap fun p =>
    if p.1 ∈ known then some s!"{whereStr}: extra key `{p.1}` collides with a format key"
    else if !p.1.startsWith "x-" then
      some s!"{whereStr}: unknown key `{p.1}` (annotation keys start with `x-`)"
    else if !(J.keysUnique p.2) then some s!"{whereStr}: duplicate keys in the value of `{p.1}`"
    else none)
  ++ (dups (ext.map (·.1))).map fun k => s!"{whereStr}: duplicate key `{k}`"

def mapErrors (whereStr : String) (keys : List String) : List String :=
  (if keys.contains "" then [s!"{whereStr}: empty name"] else [])
  ++ (dups keys).map fun k => s!"{whereStr}: duplicate name `{k}`"

def variantErrors (whereStr : String) (v : Variant) : List String :=
  (if v.to = some "" then [s!"{whereStr}: empty target `to`"] else [])
  ++ (if v.to.isNone && (v.resume || v.back) then [s!"{whereStr}: `resume` and `back` need `to`"] else [])
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

/-- The errors of the declaration of action `n`. -/
def callErrors (n : String) (c : CallDecl) : List String :=
  let w := s!"action `{n}`"
  (if c.outcomes.contains "" then [s!"{w}: empty outcome name"] else [])
  ++ (c.outcomes.filterMap fun o =>
      if o.contains ':' then some s!"{w}: outcome `{o}` contains `:`" else none)
  ++ (if c.outcomes.contains "failed" then
      [s!"{w}: `failed` is an outcome of every action with outcomes and is not listed"] else [])
  ++ (dups c.outcomes).map (fun o => s!"{w}: duplicate outcome `{o}`")
  ++ (if c.every && c.outcomes.isEmpty then [s!"{w}: `every` needs `outcomes`"] else [])
  ++ extErrors w callKeys c.ext

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
  && d.calls.all (fun p => extOKb callKeys p.2.ext)

def Doc.check (d : Doc) : List String :=
  let c := d.toChart
  let branches := c.transitions.flatMap (·.branches)
  let undeclaredCond := (branches.filterMap (·.guard)).filterMap fun n =>
    if n = "" || d.conditions.any (·.1 == n) then none
    else some s!"condition `{n}` is not described in `conditions`"
  let undeclaredCall := (branches.flatMap (·.actions)).filterMap fun
    | .call n .. => if n = "" || d.calls.any (·.1 == n) then none
                    else some s!"action `{n}` is not described in `calls`"
    | _ => none
  -- the events, timers and variants of the document are those of its chart, in document order
  let events := c.transitions.map (·.event)
  let timers := c.timers.map (·.id)
  let badTimerEvents := events.filterMap fun e =>
    if e.startsWith "timer:" && !(timers.contains (e.drop 6).toString) then
      some s!"event `{e}`: no such timer (the prefix `timer:` is reserved)"
    else none
  let outcomesOf (n : String) : Option (List String) := match lookup d.calls n with
    | some c => if c.outcomes.isEmpty then none else some ("failed" :: c.outcomes)
    | none => none
  let badOutcomeEvents := events.filterMap fun e =>
    if !e.startsWith "call:" then none
    else match splitOutcome (e.drop 5).toString with
      | none => some s!"event `{e}`: an outcome event is `call:<action>:<outcome>` (the prefix `call:` is reserved)"
      | some (n, o) =>
        if (outcomesOf n).any (·.contains o) then none
        else some s!"event `{e}`: `{n}` is not an action with the outcome `{o}` (the prefix `call:` is reserved)"
  let twiceInDo := c.transitions.flatMap fun t => t.branches.flatMap fun b =>
    (dups (waitNames b.actions)).map fun n =>
      s!"`{Chart.shown t.src}` → `{t.event}`: action `{n}` with outcomes occurs twice in `do`"
  let structural :=
    (if d.requires.all (· ∈ knownFeatures) then [] else ["document requires extensions this reader does not know"])
    ++ (match d.bot with | .sec .. => [] | .message .. => ["`bot` must be a section"])
    ++ mapErrors "conditions" (d.conditions.map (·.1))
    ++ mapErrors "calls" (d.calls.map (·.1))
    ++ d.calls.flatMap (fun p => callErrors p.1 p.2)
    ++ mapErrors "variables" (d.variables.map (·.1))
    ++ extErrors "document" docKeys d.ext
    ++ nodeErrors "bot" d.bot
  (if d.wfOK then [] else ["document violates the format rules (see the errors below)"])
  ++ structural ++ dedup undeclaredCond ++ dedup undeclaredCall ++ dedup badTimerEvents
  ++ dedup badOutcomeEvents ++ twiceInDo ++ c.lint

end Figram.Format

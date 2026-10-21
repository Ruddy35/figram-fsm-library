import Figram.FormatIO
import Figram.Compile

/-!
# Engine: the protocol "one JSON line per request, one JSON line per reply", version 1

The document is loaded and compiled once. Every request is one line, every
reply is one line with the same `id`. The full description is in docs/format.md,
section 8 "Snapshot and engine protocol".

Requests (no other fields are allowed in a request):
* `{"id": 1, "op": "hello"}` — the protocol version;
* `{"id": 2, "op": "start", "seq"?: N}` — the user's first entry (the first
  transition gets `seq` N + 1; without `seq`, 1);
* `{"id": 2, "op": "restart", "snapshot": {...}}` — start the user over,
  continuing the counter (old timer tokens never match new ones);
* `{"id": 3, "op": "step", "snapshot": {...}, "event": "btn:checkout",
   "guards": {"cart_not_empty": true}}` — a step;
  for a timer firing: `"event": "timer:<name>", "token": <token>`.

Replies:
* `{"id": 3, "snapshot": {...}, "actions": [...]}` — the step is done
  (with `"recovered": true` if the snapshot's message is no longer in the bot);
* `{"id": 3, "need": ["cart_not_empty"]}` — condition values are needed;
* `{"id": 3, "error": "...", "code": "..."}` — a request error: a text for
  humans and a machine code (`ErrCode`). If the line cannot be parsed as JSON,
  the `id` is taken from its beginning (`echoId`), otherwise it is null.

The checks that are not part of the proved `handleObj` (the strict set of request
fields, the line size limit, UTF-8) run before it: in `handle`, in
`figram-engine` and in the C shim.
-/

namespace Figram.Engine
open Figram.Format

def protocolVersion : Nat := 1
def snapshotKeys : List String := ["v", "at", "resume", "timers", "seq"]

/-! ## Snapshot -/

def encSnapshot (s : Snapshot) : J :=
  .obj ([("at", .str s.state)]
    ++ field "resume" (!s.resume.isEmpty) (.obj (s.resume.map fun p => (p.1, .str p.2)))
    ++ field "timers" (!s.timers.isEmpty) (.obj (s.timers.map fun p => (p.1, .num p.2 0)))
    ++ field "seq" (s.seq != 0) (.num s.seq 0))

/-- An integer field of the snapshot: 0 … 2^53 − 1 (as in snapshot.schema.json). -/
def boundedNat (what : String) : J → R Nat
  | .num m e =>
    match numToNat m e with
    | some n => if n ≤ maxInt then pure n else throw s!"snapshot: {what} exceeds {maxInt}"
    | none => throw s!"snapshot: {what} must be a non-negative integer"
  | _ => throw s!"snapshot: {what} must be a number"

def nonEmpty (what : String) : J → R String
  | .str s => if s.isEmpty then throw s!"snapshot: {what} is an empty string" else pure s
  | _ => throw s!"snapshot: {what} must be a string"

def decResumeEntry (p : String × J) : R (String × String) := do
  if p.1.isEmpty then throw "snapshot: empty section name in `resume`"
  pure (p.1, ← nonEmpty "a `resume` value" p.2)

def decTimerEntry (p : String × J) : R (String × Nat) := do
  if p.1.isEmpty then throw "snapshot: empty timer name"
  pure (p.1, ← boundedNat "a timer token" p.2)

def checkKeys (kvs : Obj) : R Unit :=
  match kvs.find? (fun p => decide (p.1 ∉ snapshotKeys)) with
  | some k => throw s!"snapshot: unknown field `{k.1}` (written by a newer version?)"
  | none => pure ()

def checkVersion (kvs : Obj) : R Unit :=
  match get kvs "v" with
  | none => pure ()
  | some j => do if (← boundedNat "`v`" j) = 1 then pure () else throw "snapshot: unknown version"

/-- Strict snapshot decoding; accepts exactly what snapshot.schema.json allows. -/
def decSnapshot : J → R Snapshot
  | .obj kvs => do
    checkKeys kvs
    checkVersion kvs
    let st ← match get kvs "at" with
      | some j => nonEmpty "`at`" j
      | none => throw "snapshot: missing field `at`"
    let res ← match get kvs "resume" with
      | none => pure []
      | some (.obj m) => m.mapM decResumeEntry
      | some _ => throw "snapshot: `resume` must be an object"
    let tms ← match get kvs "timers" with
      | none => pure []
      | some (.obj m) => m.mapM decTimerEntry
      | some _ => throw "snapshot: `timers` must be an object"
    let seq ← match get kvs "seq" with
      | none => pure 0
      | some j => boundedNat "`seq`" j
    pure ⟨st, res, tms, seq⟩
  | _ => throw "snapshot must be an object"

/-! ## Request errors -/

/-- The machine code of an error reply (docs/format.md, section 8, "Errors"). -/
inductive ErrCode where
  /-- the line is not JSON or not UTF-8, a duplicate key, nesting, an exponent, trailing text -/
  | parseError
  /-- the request line is longer than the limit -/
  | tooLarge
  /-- an unknown, missing or mistyped field, an unknown operation -/
  | badRequest
  /-- the snapshot failed `decSnapshot` -/
  | badSnapshot
  /-- the new `seq` would exceed 2^53 − 1 -/
  | seqOverflow
  deriving DecidableEq, Repr, Inhabited

def ErrCode.name : ErrCode → String
  | .parseError => "parse_error"
  | .tooLarge => "too_large"
  | .badRequest => "bad_request"
  | .badSnapshot => "bad_snapshot"
  | .seqOverflow => "seq_overflow"

/-- The result of handling a request: a value, or an error code with a text. -/
abbrev RE := Except (ErrCode × String)

/-- A decoding error of a part of the request gets a code. -/
@[inline] def withCode {α} (c : ErrCode) (r : R α) : RE α := r.mapError (c, ·)

def errorJ (id : J) (c : ErrCode) (text : String) : J :=
  .obj [("id", id), ("error", .str text), ("code", .str c.name)]

/-! ## Actions for the host -/

def encAction : Action → Option J
  | .send n => some (.obj [("send", .str n)])
  | .call n => some (.obj [("call", .str n)])
  | .startTimer id ms tok => some (.obj [("start_timer", .str id), ("ms", .num ms 0), ("token", .num tok 0)])
  | .cancelTimer id => some (.obj [("cancel_timer", .str id)])
  | .remember .. => none

def encActions (as : List Action) : J := .arr (as.filterMap encAction)

def result (id : J) (s : Snapshot) (as : List Action) (recovered : Bool) : J :=
  .obj ([("id", id), ("snapshot", encSnapshot s), ("actions", encActions as)]
    ++ field "recovered" recovered (.bool true))

/-! ## Request -/

/-- The request fields of operation `op`; `none` means an unknown operation (`handleObj` rejects it). -/
def opKeys : String → Option (List String)
  | "hello" => some ["id", "op"]
  | "start" => some ["id", "op", "seq"]
  | "restart" => some ["id", "op", "snapshot"]
  | "step" => some ["id", "op", "snapshot", "event", "guards", "token"]
  | _ => none

/-- A strict request: a field the operation does not define is an error (otherwise the
typo `gaurds` would silently turn into an endless `need`). Checked in `handle`
before `handleObj`. -/
def checkFields (kvs : Obj) : RE Unit :=
  match get kvs "op" with
  | some (.str op) =>
    match opKeys op with
    | some ks =>
      match kvs.find? (fun p => !ks.contains p.1) with
      | some p => throw (.badRequest, s!"unknown request field `{p.1}`")
      | none => pure ()
    | none => pure ()
  | _ => pure ()

/-- An integer request field from 0 to 2^53 − 1 by value: `5`, `5.0`, `5e0`, `-0`. -/
def reqNat (what : String) : J → R Nat
  | .num m e =>
    match numToNat m e with
    | some n => if n ≤ maxInt then pure n else throw s!"`{what}` must be an integer from 0 to {maxInt}"
    | none => throw s!"`{what}` must be an integer from 0 to {maxInt}"
  | _ => throw s!"`{what}` must be an integer from 0 to {maxInt}"

def decGuards (kvs : Obj) : R (List (String × Bool)) :=
  match get kvs "guards" with
  | none => pure []
  | some (.obj gs) => gs.mapM fun
      | (k, .bool b) => pure (k, b)
      | (k, _) => throw s!"condition `{k}`: expected true or false"
  | some _ => throw "`guards` must be an object"

/-- The request event: a non-empty string; `timer:<name>` (with a non-empty name) only
with a token, any other event without a token. -/
def decEvent (kvs : Obj) : R Event := do
  let name ← match get kvs "event" with
    | some (.str e) => if e.isEmpty then throw "`event` must be a non-empty string" else pure e
    | some _ => throw "`event` must be a non-empty string"
    | none => throw "missing field `event`"
  if name.startsWith "timer:" then
    let id := (name.drop 6).toString
    if id.isEmpty then throw "empty timer name in `event`"
    match get kvs "token" with
    | some j => pure (.timer id (← reqNat "token" j))
    | none => throw "missing field `token` for a timer event"
  else
    if (get kvs "token").isSome then throw "field `token` is allowed only for a timer event"
    pure (.named name)

/-- The condition values from the request; missing ones are `false`. -/
def envOf (gs : List (String × Bool)) : HostEnv :=
  fun n => ((gs.find? (·.1 == n)).map (·.2)).getD false

/-- The value of a condition from the request, if it was given. -/
def givenOf (gs : List (String × Bool)) (n : String) : Option Bool :=
  (gs.find? (·.1 == n)).map (·.2)

/-- The conditions the step needs but the request does not give: only those that
can affect the choice of the variant given the values already passed
(`Machine.neededGuards`; sufficiency is `missing_nil_step`). -/
def missing (m : Machine) (s : Snapshot) (ev : Event) (gs : List (String × Bool)) : List String :=
  if decide (s.state ∈ m.states) && s.accepts ev && ownsTimer (m.exitOf s.state) ev then
    (dedup (m.neededGuards s.state ev.key (givenOf gs))).filter fun n => !(gs.any (·.1 == n))
  else []

/-- A protocol step after the request has been decoded. -/
def stepReply (m : Machine) (id : J) (s : Snapshot) (ev : Event) (gs : List (String × Bool)) : RE J :=
  let miss := missing m s ev gs
  if !miss.isEmpty then pure (.obj [("id", id), ("need", .arr (miss.map .str))])
  else
    let (s', as) := m.step s ev (envOf gs)
    -- A snapshot the engine itself could not read back is never produced.
    if s'.seq > maxInt then throw (.seqOverflow, "seq_overflow")
    else pure (result id s' as (decide (s.state ∉ m.states)))

/-- The user's first entry with the counter continued from `n`: the first transition
gets `seq` n + 1, as after a snapshot with `seq` n (`start` is `startAt m 0`).
A host that starts an existing user over passes the user's persistent
high-water mark of `seq` (docs/host-contract.md, section 4). -/
def startAt (m : Machine) (n : Nat) : Snapshot × List Action :=
  realize { Snapshot.fresh with seq := n } m.initial m.initActions

def handleObj (m : Machine) (kvs : Obj) : RE J := do
  let id := (get kvs "id").getD .null
  match get kvs "op" with
  | some (.str "hello") =>
    pure (.obj [("id", id), ("protocol", .num protocolVersion 0),
      ("states", .num m.states.length 0)])
  | some (.str "start") =>
    match get kvs "seq" with
    | none =>
      let (s, as) := m.start
      pure (result id s as false)
    | some j =>
      let n ← withCode .badRequest (reqNat "seq" j)
      let (s, as) := startAt m n
      if s.seq > maxInt then throw (.seqOverflow, "seq_overflow")
      pure (result id s as false)
  | some (.str "restart") =>
    -- Start the user over without resetting the counter: cancel the user's timers,
    -- run the first-entry actions, clear the remembered places. Tokens keep growing,
    -- so late events of old timers never match new ones.
    let s ← match get kvs "snapshot" with
      | some j => withCode .badSnapshot (decSnapshot j)
      | none => throw (.badRequest, "missing field `snapshot`")
    let (s', as) := m.recover { s with resume := [] }
    if s'.seq > maxInt then throw (.seqOverflow, "seq_overflow")
    pure (result id s' as false)
  | some (.str "step") =>
    let s ← match get kvs "snapshot" with
      | some j => withCode .badSnapshot (decSnapshot j)
      | none => throw (.badRequest, "missing field `snapshot`")
    let ev ← withCode .badRequest (decEvent kvs)
    let gs ← withCode .badRequest (decGuards kvs)
    stepReply m id s ev gs
  | some (.str op) => throw (.badRequest, s!"unknown operation `{op}`")
  | some _ => throw (.badRequest, "`op` must be a string")
  | none => throw (.badRequest, "missing field `op`")

/-- The `id` from the beginning of a line that could not be parsed: `{`, the key `"id"`, `:`
and a JSON scalar (string, number, `true`, `false`, `null`) followed by `,`
or `}`; JSON whitespace is allowed between all parts. The value is as in
normal parsing (`-0` → `0`). Otherwise `null`. -/
def echoId (line : String) : J :=
  let i := skipWs line 0
  if !byteIs line i 123 then .null else
  let i := skipWs line (i + 1)
  if !(byteIs line i 34 && byteIs line (i + 1) 105 && byteIs line (i + 2) 100 && byteIs line (i + 3) 34) then
    .null else
  let i := skipWs line (i + 4)
  if !byteIs line i 58 then .null else
  let i := skipWs line (i + 1)
  let b := byteAt line i
  if !(i < line.utf8ByteSize && (b == 34 || b == 45 || isDigitB b || b == 116 || b == 102 || b == 110)) then
    .null else
  match parseValue line 0 i with
  | .ok (v, j) => if byteIs line (skipWs line j) 44 || byteIs line (skipWs line j) 125 then v else .null
  | .error _ => .null

/-- The `hello` reply of the deliberately broken test-mode engine reports the mutation. -/
def markMutant (mutant : Option String) (kvs : Obj) (j : J) : J :=
  match mutant, get kvs "op", j with
  | some spec, some (.str "hello"), .obj r => .obj (r ++ [("mutant", .str spec)])
  | _, _, j => j

/-- One request line (UTF-8 text) → one reply line; `mutant` is the mutation of the
deliberately broken test-mode engine (`figram-engine --mutate`), otherwise `none`. -/
def handleWith (mutant : Option String) (m : Machine) (line : String) : String :=
  renderCompact <| match parse line with
    | .ok (.obj kvs) =>
      match checkFields kvs >>= fun _ => handleObj m kvs with
      | .ok j => markMutant mutant kvs j
      | .error (c, e) => errorJ ((get kvs "id").getD .null) c e
    | .ok _ => errorJ .null .badRequest "request must be an object"
    | .error e => errorJ (echoId line) .parseError e

/-- One request line → one reply line. -/
def handle (m : Machine) (line : String) : String := handleWith none m line

/-! ## Lines the engine does not parse

The size limit and the UTF-8 check are done by whoever reads the bytes: the
`figram-engine` process (`EngineMain.lean`) and the C shim. The reply is an error
with the `id` from the beginning of the line (`echoId`), if it is there. -/

/-- The default size limit of a request line, in bytes (without the final `\n`). -/
def defaultMaxRequest : Nat := 1048576

def tooLargeText (limit : Nat) : String := s!"request is longer than the limit of {limit} bytes"

def invalidUtf8Text : String := "request is not valid UTF-8"

/-- The end of the longest prefix of the bytes `[0, n)` that is UTF-8 text. -/
partial def utf8PrefixEnd (b : ByteArray) (n i : Nat) : Nat :=
  if i < n then
    match b.utf8DecodeChar? i with
    | some c => if i + c.utf8Size ≤ n then utf8PrefixEnd b n (i + c.utf8Size) else i
    | none => i
  else i

/-- The prefix of the line's bytes (at most 64 KiB) that is UTF-8 text:
the `id` for the error reply is looked up in it. -/
def utf8Prefix (b : ByteArray) : String :=
  (String.fromUTF8? (b.extract 0 (utf8PrefixEnd b (min b.size 65536) 0))).getD ""

/-- The error reply to a line the engine does not parse; `pre` is its UTF-8 prefix. -/
def errorLine (pre : String) (c : ErrCode) (text : String) : String :=
  renderCompact (errorJ (echoId pre) c text)

/-- A request line as bytes → a reply line: the limit `limit` (0 means no limit),
then UTF-8, then `handleWith`. -/
def handleBytes (mutant : Option String) (limit : Nat) (m : Machine) (b : ByteArray) : String :=
  if limit != 0 && b.size > limit then errorLine (utf8Prefix b) .tooLarge (tooLargeText limit)
  else match String.fromUTF8? b with
    | some s => handleWith mutant m s
    | none => errorLine (utf8Prefix b) .parseError invalidUtf8Text

/-! ## Load conditions of the machine -/

def actionOK : Action → Bool
  | .remember sec n => !sec.isEmpty && !n.isEmpty
  | .startTimer id _ _ => !id.isEmpty
  | _ => true

/-- All names that can get into a snapshot are non-empty. -/
def namesOK (m : Machine) : Bool :=
  m.states.all (fun s => !s.isEmpty) && m.initActions.all actionOK
  && m.rows.all (fun r => r.branches.all (fun b => b.actions.all actionOK))
  && m.entry.all (fun e => e.2.all actionOK) && m.exit.all (fun e => e.2.all actionOK)

/-- Loading a document for the engine: parsing, checking, compiling and checking the
hypotheses of the protocol theorems (`wf`, `namesOK`). The document is compiled once:
`check` does not build the machine (`Chart.wfDirect`), and `wf` is checked on the built
machine in linear time (`wfFast = wf`). -/
def load (txt : String) : Except (List String) Machine :=
  match parse txt >>= decode with
  | .error e => .error [e]
  | .ok d =>
    let errs := d.check
    if !errs.isEmpty then .error errs
    else
      let m := d.toChart.compile
      if m.wfFast && namesOK m then .ok m
      else .error ["compiled machine fails the wf/namesOK check"]

end Figram.Engine

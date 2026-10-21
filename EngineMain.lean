import Figram.Engine
import Figram.Mutate

/-!
# figram-engine

* `figram-engine [--max-request BYTES] bot.json` — loads the document,
  compiles it once and serves requests line by line over stdin/stdout
  (`Figram.Engine`). A line is read as bytes: a line longer than the limit (by default
  1 MiB, 0 means no limit) gets `too_large`, a line that is not UTF-8 gets `parse_error`;
  a line of JSON whitespace only is skipped without a reply. Replies come strictly
  in request order.
* Test mode, only with `FIGRAM_ALLOW_MUTANTS=1` in the environment:
  `figram-engine --mutants bot.json` — the list of mutations for testing the tests;
  `figram-engine [--max-request BYTES] --mutate <mutation> bot.json` — the
  deliberately broken engine (a warning on stderr, `hello` reports the mutation).
-/

open Figram

/-- How many leading bytes of a too long line are kept (the `id` is looked up in them). -/
def keepPrefix : Nat := 65536

/-- A request line: its bytes without `\n` (only the beginning for a line longer than the
limit), the length in bytes and a flag "JSON whitespace only"; `none` means end of input.
Bytes are read one at a time: a block read would wait for the next requests, while the
engine answers a line as soon as it arrives. -/
partial def readLine (inp : IO.FS.Stream) (limit : Nat) (acc : ByteArray) (len : Nat) (blank : Bool) :
    IO (Option (ByteArray × Nat × Bool)) := do
  let b ← inp.read 1
  if b.isEmpty then
    return if len == 0 then none else some (acc, len, blank)
  let c := b.get! 0
  if c == 10 then
    return some (acc, len, blank)
  let blank := blank && (c == 32 || c == 9 || c == 13)
  -- the line is now longer than the limit: only its beginning is kept from here on
  let acc := if limit != 0 && len == limit && acc.size > keepPrefix then acc.extract 0 keepPrefix else acc
  let acc := if limit == 0 || len < limit || acc.size < keepPrefix then acc.push c else acc
  readLine inp limit acc (len + 1) blank

structure Cfg where
  /-- the size limit of a request line in bytes; 0 means no limit -/
  limit : Nat
  /-- the mutation of the deliberately broken test-mode engine -/
  mutant : Option String

partial def serve (cfg : Cfg) (m : Machine) (inp out : IO.FS.Stream) : IO Unit := do
  let some (b, len, blank) ← readLine inp cfg.limit .empty 0 true | return
  unless blank do
    let reply :=
      if cfg.limit != 0 && len > cfg.limit then
        Engine.errorLine (Engine.utf8Prefix b) .tooLarge (Engine.tooLargeText cfg.limit)
      else Engine.handleBytes cfg.mutant 0 m b
    out.putStr (reply ++ "\n")
    out.flush
  serve cfg m inp out

def loadOrFail (path : String) : IO (Option Machine) := do
  match Engine.load (← IO.FS.readFile path) with
  | .ok m => pure (some m)
  | .error errs =>
    for e in errs do IO.eprintln s!"document error: {Format.sanitize e}"
    pure none

/-- Leading `--max-request N`: the limit and the remaining arguments; `none` means an invalid value. -/
def parseLimit : List String → Nat → Option (Nat × List String)
  | "--max-request" :: v :: rest, _ => v.toNat? >>= parseLimit rest
  | ["--max-request"], _ => none
  | args, n => some (n, args)

def usage : String :=
  "usage: figram-engine [--max-request BYTES] <bot.json>\n" ++
  "       FIGRAM_ALLOW_MUTANTS=1 figram-engine --mutants <bot.json>\n" ++
  "       FIGRAM_ALLOW_MUTANTS=1 figram-engine [--max-request BYTES] --mutate <mutation> <bot.json>"

/-- The number or name in a mutation refers to this machine: otherwise, for example,
`redirect:999` or `drop-call:no_such_call` would silently give an unbroken engine.
Mutations without a number (`drop-exit` and the like) apply to any machine. -/
def specInRange (m : Machine) (spec : String) : Bool :=
  match spec.splitOn ":" with
  | ["drop-call", n] => (Mutate.callNames m).contains n
  | [kind, k] =>
    match k.toNat? with
    | some k =>
      if kind == "swap-branches" then k < m.rows.length
      else if kind == "reverse-entry" then k < m.entry.length
      else k < (Mutate.branches m).length
    | none => false
  | _ => true

/-- The test mode of the deliberately broken engine is enabled only explicitly. -/
def mutantsAllowed : IO Bool := return (← IO.getEnv "FIGRAM_ALLOW_MUTANTS") == some "1"

def mutantsRefused : String :=
  "figram-engine: --mutants and --mutate are a test mode (a deliberately broken engine); " ++
  "enable it with the environment variable FIGRAM_ALLOW_MUTANTS=1"

def main (args : List String) : IO UInt32 := do
  let some (limit, args) := parseLimit args Engine.defaultMaxRequest
    | IO.eprintln usage; return 2
  match args with
  | [path] =>
    let some m ← loadOrFail path | return 1
    serve ⟨limit, none⟩ m (← IO.getStdin) (← IO.getStdout)
    return 0
  | ["--mutants", path] =>
    unless (← mutantsAllowed) do
      IO.eprintln mutantsRefused
      return 2
    let some m ← loadOrFail path | return 1
    for s in Mutate.mutants m do IO.println s
    return 0
  | ["--mutate", spec, path] =>
    unless (← mutantsAllowed) do
      IO.eprintln mutantsRefused
      return 2
    let some m ← loadOrFail path | return 1
    let some m' := Mutate.apply m spec
      | IO.eprintln s!"unknown mutation `{Format.sanitize spec}` (see --mutants for the list)"; return 2
    unless specInRange m spec do
      IO.eprintln s!"mutation `{Format.sanitize spec}` does not apply to this machine (see --mutants for the list)"
      return 2
    -- the hypotheses of the protocol theorems are checked for the broken machine too
    -- (`wfFast` is the same `wf` check in linear time, theorem `Machine.wfFast_eq`)
    unless m'.wfFast && Engine.namesOK m' do
      IO.eprintln s!"mutant `{Format.sanitize spec}` fails the wf/namesOK check"
      return 1
    IO.eprintln s!"figram-engine: WARNING: deliberately broken engine (mutation `{Format.sanitize spec}`), \
      for testing the tests only"
    serve ⟨limit, some spec⟩ m' (← IO.getStdin) (← IO.getStdout)
    return 0
  | _ =>
    IO.eprintln usage
    return 2

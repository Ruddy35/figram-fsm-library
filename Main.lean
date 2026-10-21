import Figram
import Figram.Trace
import Figram.RandomDoc

/-!
# CLI `figram`

* `figram example` — the pizza shop bot document;
* `figram check <bot.json>` — the errors of a document (exit status 1); warnings go
  to stderr with the prefix `warning:`, exit status 0;
* `figram fmt <bot.json>` — the canonical form;
* `figram stats <bot.json>` — the size of the compiled machine;
* `figram trace <bot.json> <count> <length> <seed>` — random reference
  scenarios (JSONL); the coverage of arrow variants goes to stderr;
* `figram random <seed> <messages>` — a random valid document;
* `figram golden <dir>` — the fixed scenarios and their documents;
* `figram json` — parse the lines of stdin with the text layer (for tests).

Any error gives a non-zero exit status.
-/

open Figram Figram.Format

def usage : String :=
  "usage: figram example | check <bot.json> | fmt <bot.json> | stats <bot.json>\n" ++
  "       | trace <bot.json> <count> <length> <seed> | random <seed> <messages> | golden <dir>"

def readDoc (path : String) : IO Doc := do
  let txt ← IO.FS.readFile path
  match parse txt >>= decode with
  | .ok d => pure d
  | .error e => throw <| IO.userError s!"не удалось прочитать документ: {sanitize e}"

def checked (d : Doc) : IO Chart := do
  let errs := d.check
  unless errs.isEmpty do
    for e in errs do IO.eprintln s!"ошибка: {sanitize e}"
    throw <| IO.userError "документ не прошёл проверку"
  pure d.toChart

/-- A strictly positive number from an argument. -/
def posArg (name val : String) : IO Nat :=
  match val.toNat? with
  | some n => if n > 0 then pure n else throw <| IO.userError s!"{name} должно быть больше нуля"
  | none => throw <| IO.userError s!"{name}: ожидалось целое число, получено «{val}»"

def natArg (name val : String) : IO Nat :=
  match val.toNat? with
  | some n => pure n
  | none => throw <| IO.userError s!"{name}: ожидалось целое число, получено «{val}»"

/-- `figram json`, for differential testing of the text layer: a line in,
`OK <compact form>` or `ERR <reason>` out. The input is read as a whole, as bytes;
a line that is not valid UTF-8 gives `ERR`, as in the engine. -/
partial def jsonLines (data : ByteArray) (out : IO.FS.Stream) (start : Nat) : IO Unit := do
  if start ≥ data.size then return
  let stop := (data.findIdx? (· == 10) start).getD data.size
  let reply := match String.fromUTF8? (data.extract start stop) with
    | none => "ERR " ++ renderCompact (.str "текст не в кодировке UTF-8")
    | some text =>
      match parse text with
      | .ok j => "OK " ++ renderCompact j
      | .error e => "ERR " ++ renderCompact (.str e)
  out.putStr (reply ++ "\n")
  jsonLines data out (stop + 1)

def run (args : List String) : IO Unit := do
  match args with
  | ["example"] => IO.println (print (encode Examples.pizzaDoc))
  | ["check", p] =>
    let d ← readDoc p
    let errs := d.check
    if errs.isEmpty then
      IO.println "ok"
      for w in d.warnings do IO.eprintln s!"предупреждение: {sanitize w}"
    else
      for e in errs do IO.println (sanitize e)
      throw <| IO.userError s!"ошибок: {errs.length}"
  | ["fmt", p] => IO.println (print (encode (← readDoc p)))
  | ["stats", p] =>
    let m := (← checked (← readDoc p)).compile
    let branches : Nat := m.rows.foldl (fun a r => a + r.branches.length) 0
    let actions : Nat := m.entry.foldl (fun (a : Nat) e => a + e.2.length) 0
      + m.exit.foldl (fun (a : Nat) e => a + e.2.length) 0
      + m.rows.foldl (fun (a : Nat) r => a + r.branches.foldl (fun (b : Nat) x => b + x.actions.length) 0) 0
    IO.println (renderCompact (.obj [("states", .num m.states.length 0), ("rows", .num m.rows.length 0),
      ("branches", .num branches 0), ("resumes", .num m.resumes.length 0),
      ("actions", .num actions 0)]))
  | ["trace", p, count, len, seed] =>
    let c ← checked (← readDoc p)
    let count ← posArg "count" count
    let len ← posArg "length" len
    let seed ← natArg "seed" seed
    let mut covered : List String := []
    for i in List.range count do
      let (j, cov) := Trace.random c len (seed + i * 7919)
      IO.println (renderCompact j)
      covered := cov.foldl (fun acc x => if acc.contains x then acc else x :: acc) covered
    IO.eprintln s!"покрыто вариантов стрелок: {covered.length} из {Trace.totalBranches c}"
  | ["random", seed, n] =>
    let d := RandomDoc.doc (← natArg "seed" seed) (← posArg "messages" n)
    let errs := d.check
    unless errs.isEmpty do
      throw <| IO.userError s!"генератор создал некорректный документ: {errs}"
    IO.println (print (encode d))
  | ["golden", dir] =>
    IO.FS.createDirAll dir
    let pizzaText := print (encode Examples.pizzaDoc)
    IO.FS.writeFile s!"{dir}/pizza.bot.json" (pizzaText ++ "\n")
    IO.FS.writeFile s!"{dir}/features.bot.json" (print (encode Examples.featuresDoc) ++ "\n")
    let mut lines : Array String := #[]
    for (name, d, from?, ins) in Examples.golden do
      let docName := if print (encode d) == pizzaText then "pizza.bot.json" else "features.bot.json"
      let (j, _, _) := Trace.scripted d.toChart name from? ins
      let j := match j with
        | .obj kvs => J.obj (("doc", .str docName) :: kvs)
        | other => other
      lines := lines.push (renderCompact j)
    IO.FS.writeFile s!"{dir}/golden.jsonl" ("\n".intercalate lines.toList ++ "\n")
    IO.println s!"сценариев: {lines.size}"
  | ["json"] => jsonLines (← (← IO.getStdin).readBinToEnd) (← IO.getStdout) 0
  | _ => throw <| IO.userError usage

def main (args : List String) : IO UInt32 := do
  try
    run args
    return 0
  catch e =>
    IO.eprintln s!"figram: {e}"
    return 1

import Figram.Engine
import Figram.Mutate

/-!
# figram-engine

* `figram-engine bot.json` — загружает документ, компилирует один раз
  и обслуживает запросы построчно через stdin/stdout (`Figram.Engine`);
* `figram-engine --mutants bot.json` — список мутаций для проверки тестов;
* `figram-engine --mutate <мутация> bot.json` — испорченный движок.
-/

open Figram

partial def loop (m : Machine) (inp out : IO.FS.Stream) : IO Unit := do
  let line ← inp.getLine
  if line.isEmpty then return
  unless line.all Char.isWhitespace do
    out.putStr (Engine.handle m line ++ "\n")
    out.flush
  loop m inp out

def loadOrFail (path : String) : IO (Option Machine) := do
  match Engine.load (← IO.FS.readFile path) with
  | .ok m => pure (some m)
  | .error errs =>
    for e in errs do IO.eprintln s!"ошибка документа: {e}"
    pure none

def main (args : List String) : IO UInt32 := do
  match args with
  | [path] =>
    let some m ← loadOrFail path | return 1
    loop m (← IO.getStdin) (← IO.getStdout)
    return 0
  | ["--mutants", path] =>
    let some m ← loadOrFail path | return 1
    for s in Mutate.mutants m do IO.println s
    return 0
  | ["--mutate", spec, path] =>
    let some m ← loadOrFail path | return 1
    let some m' := Mutate.apply m spec
      | IO.eprintln s!"неизвестная мутация «{spec}»"; return 2
    loop m' (← IO.getStdin) (← IO.getStdout)
    return 0
  | _ =>
    IO.eprintln "usage: figram-engine <bot.json> | --mutants <bot.json> | --mutate <мутация> <bot.json>"
    return 2

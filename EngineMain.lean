import Figram.Engine
import Figram.Mutate

/-!
# figram-engine

* `figram-engine [--max-request BYTES] bot.json` — загружает документ,
  компилирует один раз и обслуживает запросы построчно через stdin/stdout
  (`Figram.Engine`). Строка читается байтами: длиннее предела (по умолчанию
  1 МиБ, 0 — без предела) — ответ `too_large`, не UTF-8 — `parse_error`;
  строка из одних пробелов JSON пропускается без ответа. Ответы идут строго
  в порядке запросов.
* Тестовый режим, только при `FIGRAM_ALLOW_MUTANTS=1` в окружении:
  `figram-engine --mutants bot.json` — список мутаций для проверки тестов;
  `figram-engine [--max-request BYTES] --mutate <мутация> bot.json` —
  испорченный движок (предупреждение в stderr, `hello` сообщает мутацию).
-/

open Figram

/-- Сколько первых байт слишком длинной строки хранится (по ним ищется `id`). -/
def keepPrefix : Nat := 65536

/-- Строка запроса: её байты без `\n` (у строки длиннее предела — только начало),
длина в байтах и признак «только пробелы JSON»; `none` — конец ввода.
Байты читаются по одному: чтение блоком ждало бы следующих запросов, а движок
отвечает на строку сразу, как она пришла. -/
partial def readLine (inp : IO.FS.Stream) (limit : Nat) (acc : ByteArray) (len : Nat) (blank : Bool) :
    IO (Option (ByteArray × Nat × Bool)) := do
  let b ← inp.read 1
  if b.isEmpty then
    return if len == 0 then none else some (acc, len, blank)
  let c := b.get! 0
  if c == 10 then
    return some (acc, len, blank)
  let blank := blank && (c == 32 || c == 9 || c == 13)
  -- строка стала длиннее предела: дальше хранится только начало
  let acc := if limit != 0 && len == limit && acc.size > keepPrefix then acc.extract 0 keepPrefix else acc
  let acc := if limit == 0 || len < limit || acc.size < keepPrefix then acc.push c else acc
  readLine inp limit acc (len + 1) blank

structure Cfg where
  /-- предел размера строки запроса в байтах; 0 — без предела -/
  limit : Nat
  /-- мутация испорченного движка тестового режима -/
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
    for e in errs do IO.eprintln s!"ошибка документа: {Format.sanitize e}"
    pure none

/-- Ведущие `--max-request N`: предел и остальные аргументы; `none` — неверное значение. -/
def parseLimit : List String → Nat → Option (Nat × List String)
  | "--max-request" :: v :: rest, _ => v.toNat? >>= parseLimit rest
  | ["--max-request"], _ => none
  | args, n => some (n, args)

def usage : String :=
  "usage: figram-engine [--max-request BYTES] <bot.json>\n" ++
  "       FIGRAM_ALLOW_MUTANTS=1 figram-engine --mutants <bot.json>\n" ++
  "       FIGRAM_ALLOW_MUTANTS=1 figram-engine [--max-request BYTES] --mutate <мутация> <bot.json>"

/-- Тестовый режим испорченного движка включается только явно. -/
def mutantsAllowed : IO Bool := return (← IO.getEnv "FIGRAM_ALLOW_MUTANTS") == some "1"

def mutantsRefused : String :=
  "figram-engine: --mutants и --mutate — тестовый режим (испорченный движок); " ++
  "он включается переменной окружения FIGRAM_ALLOW_MUTANTS=1"

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
    -- только мутации из списка `--mutants`: иначе, например, `redirect:999`
    -- молча дал бы неиспорченный движок
    unless (Mutate.mutants m).contains spec do
      IO.eprintln s!"неизвестная мутация «{Format.sanitize spec}» (список — --mutants)"
      return 2
    let some m' := Mutate.apply m spec
      | IO.eprintln s!"неизвестная мутация «{Format.sanitize spec}»"; return 2
    -- условия теорем о протоколе проверяются и у испорченного автомата
    unless m'.wf && Engine.namesOK m' do
      IO.eprintln s!"мутант «{Format.sanitize spec}» не прошёл проверку wf/namesOK"
      return 1
    IO.eprintln s!"figram-engine: ВНИМАНИЕ: испорченный движок (мутация «{Format.sanitize spec}») — \
      только для проверки тестов"
    serve ⟨limit, some spec⟩ m' (← IO.getStdin) (← IO.getStdout)
    return 0
  | _ =>
    IO.eprintln usage
    return 2

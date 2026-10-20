import Figram.FormatIO
import Figram.Compile

/-!
# Движок: протокол «строка JSON на запрос, строка JSON на ответ», версия 1

Документ загружается и компилируется один раз. Каждый запрос — одна строка,
каждый ответ — одна строка с тем же `id`. Полное описание — FORMAT.md,
раздел «Снимок и протокол движка».

Запросы (других полей в запросе быть не должно):
* `{"id": 1, "op": "hello"}` — версия протокола;
* `{"id": 2, "op": "start", "seq"?: N}` — первый вход пользователя (первый
  переход получает `seq` N + 1; без `seq` — 1);
* `{"id": 2, "op": "restart", "snapshot": {...}}` — начать пользователя заново,
  продолжая счётчик (жетоны старых таймеров не совпадут с новыми);
* `{"id": 3, "op": "step", "snapshot": {...}, "event": "btn:checkout",
   "guards": {"cart_not_empty": true}}` — шаг;
  для срабатывания таймера: `"event": "timer:<имя>", "token": <жетон>`.

Ответы:
* `{"id": 3, "snapshot": {...}, "actions": [...]}` — шаг выполнен
  (с `"recovered": true`, если сообщения из снимка больше нет в боте);
* `{"id": 3, "need": ["cart_not_empty"]}` — нужны значения условий;
* `{"id": 3, "error": "...", "code": "..."}` — ошибка запроса: текст для
  человека и машинный код (`ErrCode`). Если строка не разобрана как JSON,
  `id` берётся из её начала (`echoId`), иначе — null.

Проверки, которых нет в доказанной `handleObj` (строгий набор полей запроса,
предел размера строки, кодировка UTF-8), выполняются до неё: `handle`,
`figram-engine`, прослойка C.
-/

namespace Figram.Engine
open Figram.Format

def protocolVersion : Nat := 1
def snapshotKeys : List String := ["v", "at", "resume", "timers", "seq"]

/-! ## Снимок -/

def encSnapshot (s : Snapshot) : J :=
  .obj ([("at", .str s.state)]
    ++ field "resume" (!s.resume.isEmpty) (.obj (s.resume.map fun p => (p.1, .str p.2)))
    ++ field "timers" (!s.timers.isEmpty) (.obj (s.timers.map fun p => (p.1, .num p.2 0)))
    ++ field "seq" (s.seq != 0) (.num s.seq 0))

/-- Целое поле снимка: 0 … 2^53 − 1 (как в snapshot.schema.json). -/
def boundedNat (what : String) : J → R Nat
  | .num m e =>
    match numToNat m e with
    | some n => if n ≤ maxInt then pure n else throw s!"снимок: {what} больше {maxInt}"
    | none => throw s!"снимок: {what} должно быть неотрицательным целым"
  | _ => throw s!"снимок: {what} должно быть числом"

def nonEmpty (what : String) : J → R String
  | .str s => if s.isEmpty then throw s!"снимок: {what} — пустая строка" else pure s
  | _ => throw s!"снимок: {what} должно быть строкой"

def decResumeEntry (p : String × J) : R (String × String) := do
  if p.1.isEmpty then throw "снимок: пустое имя раздела в «resume»"
  pure (p.1, ← nonEmpty "значение «resume»" p.2)

def decTimerEntry (p : String × J) : R (String × Nat) := do
  if p.1.isEmpty then throw "снимок: пустое имя таймера"
  pure (p.1, ← boundedNat "жетон таймера" p.2)

def checkKeys (kvs : Obj) : R Unit :=
  match kvs.find? (fun p => decide (p.1 ∉ snapshotKeys)) with
  | some k => throw s!"снимок: неизвестное поле «{k.1}» (снимок более новой версии?)"
  | none => pure ()

def checkVersion (kvs : Obj) : R Unit :=
  match get kvs "v" with
  | none => pure ()
  | some j => do if (← boundedNat "«v»" j) = 1 then pure () else throw "снимок: неизвестная версия"

/-- Строгое чтение снимка; принимает ровно то, что разрешает snapshot.schema.json. -/
def decSnapshot : J → R Snapshot
  | .obj kvs => do
    checkKeys kvs
    checkVersion kvs
    let st ← match get kvs "at" with
      | some j => nonEmpty "«at»" j
      | none => throw "у снимка нужно поле «at»"
    let res ← match get kvs "resume" with
      | none => pure []
      | some (.obj m) => m.mapM decResumeEntry
      | some _ => throw "снимок: «resume» должен быть объектом"
    let tms ← match get kvs "timers" with
      | none => pure []
      | some (.obj m) => m.mapM decTimerEntry
      | some _ => throw "снимок: «timers» должен быть объектом"
    let seq ← match get kvs "seq" with
      | none => pure 0
      | some j => boundedNat "«seq»" j
    pure ⟨st, res, tms, seq⟩
  | _ => throw "снимок должен быть объектом"

/-! ## Ошибки запроса -/

/-- Машинный код ответа-ошибки (FORMAT.md §7, «Ошибки»). -/
inductive ErrCode where
  /-- строка не JSON, не UTF-8, повтор ключа, вложенность, показатель, лишний текст -/
  | parseError
  /-- строка запроса длиннее предела -/
  | tooLarge
  /-- неизвестное, недостающее или неверного типа поле, неизвестная операция -/
  | badRequest
  /-- снимок не прошёл `decSnapshot` -/
  | badSnapshot
  /-- новый `seq` превысил бы 2^53 − 1 -/
  | seqOverflow
  deriving DecidableEq, Repr, Inhabited

def ErrCode.name : ErrCode → String
  | .parseError => "parse_error"
  | .tooLarge => "too_large"
  | .badRequest => "bad_request"
  | .badSnapshot => "bad_snapshot"
  | .seqOverflow => "seq_overflow"

/-- Результат обработки запроса: значение или код ошибки с текстом. -/
abbrev RE := Except (ErrCode × String)

/-- Ошибка разбора части запроса получает код. -/
@[inline] def withCode {α} (c : ErrCode) (r : R α) : RE α := r.mapError (c, ·)

def errorJ (id : J) (c : ErrCode) (text : String) : J :=
  .obj [("id", id), ("error", .str text), ("code", .str c.name)]

/-! ## Действия для хоста -/

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

/-! ## Запрос -/

/-- Поля запроса операции `op`; `none` — неизвестная операция (её отвергает `handleObj`). -/
def opKeys : String → Option (List String)
  | "hello" => some ["id", "op"]
  | "start" => some ["id", "op", "seq"]
  | "restart" => some ["id", "op", "snapshot"]
  | "step" => some ["id", "op", "snapshot", "event", "guards", "token"]
  | _ => none

/-- Строгий запрос: поле, не предусмотренное для операции, — ошибка (опечатка
`gaurds` иначе молча превращалась бы в бесконечный `need`). Проверяется в
`handle` до `handleObj`. -/
def checkFields (kvs : Obj) : RE Unit :=
  match get kvs "op" with
  | some (.str op) =>
    match opKeys op with
    | some ks =>
      match kvs.find? (fun p => !ks.contains p.1) with
      | some p => throw (.badRequest, s!"неизвестное поле запроса «{p.1}»")
      | none => pure ()
    | none => pure ()
  | _ => pure ()

/-- Целое поле запроса от 0 до 2^53 − 1 по значению: `5`, `5.0`, `5e0`, `-0`. -/
def reqNat (what : String) : J → R Nat
  | .num m e =>
    match numToNat m e with
    | some n => if n ≤ maxInt then pure n else throw s!"«{what}» должен быть целым от 0 до {maxInt}"
    | none => throw s!"«{what}» должен быть целым от 0 до {maxInt}"
  | _ => throw s!"«{what}» должен быть целым от 0 до {maxInt}"

def decGuards (kvs : Obj) : R (List (String × Bool)) :=
  match get kvs "guards" with
  | none => pure []
  | some (.obj gs) => gs.mapM fun
      | (k, .bool b) => pure (k, b)
      | (k, _) => throw s!"условие «{k}»: ожидалось true или false"
  | some _ => throw "«guards» должен быть объектом"

/-- Событие запроса: непустая строка; `timer:<имя>` (имя непустое) — только с
жетоном, любое другое — без жетона. -/
def decEvent (kvs : Obj) : R Event := do
  let name ← match get kvs "event" with
    | some (.str e) => if e.isEmpty then throw "«event» должно быть непустой строкой" else pure e
    | some _ => throw "«event» должно быть непустой строкой"
    | none => throw "нужно поле «event»"
  if name.startsWith "timer:" then
    let id := (name.drop 6).toString
    if id.isEmpty then throw "пустое имя таймера в «event»"
    match get kvs "token" with
    | some j => pure (.timer id (← reqNat "token" j))
    | none => throw "для события таймера нужно поле «token»"
  else
    if (get kvs "token").isSome then throw "поле «token» допустимо только для события таймера"
    pure (.named name)

/-- Значения условий из запроса; отсутствующие — `false`. -/
def envOf (gs : List (String × Bool)) : HostEnv :=
  fun n => ((gs.find? (·.1 == n)).map (·.2)).getD false

/-- Значение условия из запроса, если оно передано. -/
def givenOf (gs : List (String × Bool)) (n : String) : Option Bool :=
  (gs.find? (·.1 == n)).map (·.2)

/-- Условия, которые нужны шагу, но не переданы в запросе: только те, что
могут повлиять на выбор варианта при уже переданных значениях
(`Machine.neededGuards`; достаточность — `missing_nil_step`). -/
def missing (m : Machine) (s : Snapshot) (ev : Event) (gs : List (String × Bool)) : List String :=
  if decide (s.state ∈ m.states) && s.accepts ev && ownsTimer (m.exitOf s.state) ev then
    (dedup (m.neededGuards s.state ev.key (givenOf gs))).filter fun n => !(gs.any (·.1 == n))
  else []

/-- Шаг протокола после разбора запроса. -/
def stepReply (m : Machine) (id : J) (s : Snapshot) (ev : Event) (gs : List (String × Bool)) : RE J :=
  let miss := missing m s ev gs
  if !miss.isEmpty then pure (.obj [("id", id), ("need", .arr (miss.map .str))])
  else
    let (s', as) := m.step s ev (envOf gs)
    -- Снимок, который движок сам не сможет прочитать, не выдаётся.
    if s'.seq > maxInt then throw (.seqOverflow, "seq_overflow")
    else pure (result id s' as (decide (s.state ∉ m.states)))

/-- Первый вход пользователя со счётчиком, продолженным с `n`: первый переход
получает `seq` n + 1, как после снимка с `seq` n (`start` — это `startAt m 0`).
Хост, начинающий заново существующего пользователя, передаёт его постоянную
верхнюю отметку `seq` (HOST.md, раздел 4). -/
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
    -- Начать пользователя заново, не сбрасывая счётчик: отмена его таймеров,
    -- действия первого входа, запомненные места очищаются. Жетоны продолжают
    -- расти, поэтому запоздавшие события старых таймеров не совпадут с новыми.
    let s ← match get kvs "snapshot" with
      | some j => withCode .badSnapshot (decSnapshot j)
      | none => throw (.badRequest, "нужно поле «snapshot»")
    let (s', as) := m.recover { s with resume := [] }
    if s'.seq > maxInt then throw (.seqOverflow, "seq_overflow")
    pure (result id s' as false)
  | some (.str "step") =>
    let s ← match get kvs "snapshot" with
      | some j => withCode .badSnapshot (decSnapshot j)
      | none => throw (.badRequest, "нужно поле «snapshot»")
    let ev ← withCode .badRequest (decEvent kvs)
    let gs ← withCode .badRequest (decGuards kvs)
    stepReply m id s ev gs
  | some (.str op) => throw (.badRequest, s!"неизвестная операция «{op}»")
  | some _ => throw (.badRequest, "«op» должно быть строкой")
  | none => throw (.badRequest, "нужно поле «op»")

/-- `id` из начала строки, которую не удалось разобрать: `{`, ключ `"id"`, `:`
и скаляр JSON (строка, число, `true`, `false`, `null`), за которым следует `,`
или `}`; пробелы JSON допускаются между всеми частями. Значение — как при
обычном разборе (`-0` → `0`). Иначе — `null`. -/
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

/-- Ответ `hello` испорченного движка тестового режима сообщает мутацию. -/
def markMutant (mutant : Option String) (kvs : Obj) (j : J) : J :=
  match mutant, get kvs "op", j with
  | some spec, some (.str "hello"), .obj r => .obj (r ++ [("mutant", .str spec)])
  | _, _, j => j

/-- Одна строка запроса (текст UTF-8) → одна строка ответа; `mutant` — мутация
испорченного движка тестового режима (`figram-engine --mutate`), иначе `none`. -/
def handleWith (mutant : Option String) (m : Machine) (line : String) : String :=
  renderCompact <| match parse line with
    | .ok (.obj kvs) =>
      match checkFields kvs >>= fun _ => handleObj m kvs with
      | .ok j => markMutant mutant kvs j
      | .error (c, e) => errorJ ((get kvs "id").getD .null) c e
    | .ok _ => errorJ .null .badRequest "запрос должен быть объектом"
    | .error e => errorJ (echoId line) .parseError e

/-- Одна строка запроса → одна строка ответа. -/
def handle (m : Machine) (line : String) : String := handleWith none m line

/-! ## Строки, которые движок не разбирает

Предел размера и проверку UTF-8 выполняет тот, кто читает байты: процесс
`figram-engine` (`EngineMain.lean`) и прослойка C. Ответ — ошибка с `id` из
начала строки (`echoId`), если оно там есть. -/

/-- Предел размера строки запроса по умолчанию, байт (без завершающего `\n`). -/
def defaultMaxRequest : Nat := 1048576

def tooLargeText (limit : Nat) : String := s!"запрос длиннее предела {limit} байт"

def invalidUtf8Text : String := "запрос не в кодировке UTF-8"

/-- Конец самого длинного начала байтов `[0, n)`, которое является текстом UTF-8. -/
partial def utf8PrefixEnd (b : ByteArray) (n i : Nat) : Nat :=
  if i < n then
    match b.utf8DecodeChar? i with
    | some c => if i + c.utf8Size ≤ n then utf8PrefixEnd b n (i + c.utf8Size) else i
    | none => i
  else i

/-- Начало байтов строки (не больше 64 КиБ), которое является текстом UTF-8:
по нему ищется `id` для ответа-ошибки. -/
def utf8Prefix (b : ByteArray) : String :=
  (String.fromUTF8? (b.extract 0 (utf8PrefixEnd b (min b.size 65536) 0))).getD ""

/-- Ответ-ошибка на строку, которую движок не разбирает; `pre` — её начало в UTF-8. -/
def errorLine (pre : String) (c : ErrCode) (text : String) : String :=
  renderCompact (errorJ (echoId pre) c text)

/-- Строка запроса в байтах → строка ответа: предел `limit` (0 — без предела),
затем UTF-8, затем `handleWith`. -/
def handleBytes (mutant : Option String) (limit : Nat) (m : Machine) (b : ByteArray) : String :=
  if limit != 0 && b.size > limit then errorLine (utf8Prefix b) .tooLarge (tooLargeText limit)
  else match String.fromUTF8? b with
    | some s => handleWith mutant m s
    | none => errorLine (utf8Prefix b) .parseError invalidUtf8Text

/-! ## Условия загрузки автомата -/

def actionOK : Action → Bool
  | .remember sec n => !sec.isEmpty && !n.isEmpty
  | .startTimer id _ _ => !id.isEmpty
  | _ => true

/-- Все имена, которые могут попасть в снимок, непусты. -/
def namesOK (m : Machine) : Bool :=
  m.states.all (fun s => !s.isEmpty) && m.initActions.all actionOK
  && m.rows.all (fun r => r.branches.all (fun b => b.actions.all actionOK))
  && m.entry.all (fun e => e.2.all actionOK) && m.exit.all (fun e => e.2.all actionOK)

/-- Загрузка документа для движка: разбор, проверка, компиляция и проверка
условий теорем о протоколе (`wf`, `namesOK`). Документ компилируется один раз:
`check` автомат не строит (`Chart.wfDirect`), `wf` проверяется на построенном
автомате за линейное время (`wfFast = wf`). -/
def load (txt : String) : Except (List String) Machine :=
  match parse txt >>= decode with
  | .error e => .error [e]
  | .ok d =>
    let errs := d.check
    if !errs.isEmpty then .error errs
    else
      let m := d.toChart.compile
      if m.wfFast && namesOK m then .ok m
      else .error ["скомпилированный автомат не прошёл проверку wf/namesOK"]

end Figram.Engine

import Figram.FormatIO
import Figram.Compile

/-!
# Движок: протокол «строка JSON на запрос, строка JSON на ответ», версия 1

Документ загружается и компилируется один раз. Каждый запрос — одна строка,
каждый ответ — одна строка с тем же `id`. Полное описание — FORMAT.md,
раздел «Снимок и протокол движка».

Запросы:
* `{"id": 1, "op": "hello"}` — версия протокола;
* `{"id": 2, "op": "start"}` — первый вход пользователя;
* `{"id": 2, "op": "restart", "snapshot": {...}}` — начать пользователя заново,
  продолжая счётчик (жетоны старых таймеров не совпадут с новыми);
* `{"id": 3, "op": "step", "snapshot": {...}, "event": "btn:checkout",
   "guards": {"cart_not_empty": true}}` — шаг;
  для срабатывания таймера: `"event": "timer:<имя>", "token": <жетон>`.

Ответы:
* `{"id": 3, "snapshot": {...}, "actions": [...]}` — шаг выполнен
  (с `"recovered": true`, если сообщения из снимка больше нет в боте);
* `{"id": 3, "need": ["cart_not_empty"]}` — нужны значения условий;
* `{"id": 3, "error": "..."}` — ошибка запроса.
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

def decGuards (kvs : Obj) : R (List (String × Bool)) :=
  match get kvs "guards" with
  | none => pure []
  | some (.obj gs) => gs.mapM fun
      | (k, .bool b) => pure (k, b)
      | (k, _) => throw s!"условие «{k}»: ожидалось true или false"
  | some _ => throw "«guards» должен быть объектом"

def decEvent (kvs : Obj) : R Event := do
  let name ← match get kvs "event" with
    | some (.str e) => pure e
    | _ => throw "нужно поле «event»"
  if name.startsWith "timer:" then
    match get kvs "token" with
    | some (.num m e) =>
      match numToNat m e with
      | some t => pure (.timer (name.drop 6).toString t)
      | none => throw "«token» должен быть неотрицательным целым"
    | _ => throw "для события таймера нужно поле «token»"
  else pure (.named name)

/-- Значения условий из запроса; отсутствующие — `false`. -/
def envOf (gs : List (String × Bool)) : HostEnv :=
  fun n => ((gs.find? (·.1 == n)).map (·.2)).getD false

/-- Условия, которые нужны шагу, но не переданы в запросе. -/
def missing (m : Machine) (s : Snapshot) (ev : Event) (gs : List (String × Bool)) : List String :=
  if decide (s.state ∈ m.states) && s.accepts ev then
    (dedup (m.requiredGuards s.state ev.key)).filter fun n => !(gs.any (·.1 == n))
  else []

/-- Шаг протокола после разбора запроса. -/
def stepReply (m : Machine) (id : J) (s : Snapshot) (ev : Event) (gs : List (String × Bool)) : R J :=
  let miss := missing m s ev gs
  if !miss.isEmpty then pure (.obj [("id", id), ("need", .arr (miss.map .str))])
  else
    let (s', as) := m.step s ev (envOf gs)
    -- Снимок, который движок сам не сможет прочитать, не выдаётся.
    if s'.seq > maxInt then throw "seq_overflow"
    else pure (result id s' as (decide (s.state ∉ m.states)))

def handleObj (m : Machine) (kvs : Obj) : R J := do
  let id := (get kvs "id").getD .null
  match get kvs "op" with
  | some (.str "hello") =>
    pure (.obj [("id", id), ("protocol", .num protocolVersion 0),
      ("states", .num m.states.length 0)])
  | some (.str "start") =>
    let (s, as) := m.start
    pure (result id s as false)
  | some (.str "restart") =>
    -- Начать пользователя заново, не сбрасывая счётчик: отмена его таймеров,
    -- действия первого входа, запомненные места очищаются. Жетоны продолжают
    -- расти, поэтому запоздавшие события старых таймеров не совпадут с новыми.
    let s ← match get kvs "snapshot" with
      | some j => decSnapshot j
      | none => throw "нужно поле «snapshot»"
    let (s', as) := m.recover { s with resume := [] }
    if s'.seq > maxInt then throw "seq_overflow"
    pure (result id s' as false)
  | some (.str "step") =>
    let s ← match get kvs "snapshot" with
      | some j => decSnapshot j
      | none => throw "нужно поле «snapshot»"
    let ev ← decEvent kvs
    let gs ← decGuards kvs
    stepReply m id s ev gs
  | _ => throw "неизвестная операция «op»"

/-- Одна строка запроса → одна строка ответа. -/
def handle (m : Machine) (line : String) : String :=
  let out := match parse line with
    | .ok (.obj kvs) =>
      match handleObj m kvs with
      | .ok j => j
      | .error e => .obj [("id", (get kvs "id").getD .null), ("error", .str e)]
    | .ok _ => .obj [("id", .null), ("error", .str "запрос должен быть объектом")]
    | .error e => .obj [("id", .null), ("error", .str e)]
  renderCompact out

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
условий теорем о протоколе (`wf`, `namesOK`). -/
def load (txt : String) : Except (List String) Machine :=
  match parse txt >>= decode with
  | .error e => .error [e]
  | .ok d =>
    let errs := d.check
    if !errs.isEmpty then .error errs
    else
      let m := d.toChart.compile
      if m.wf && namesOK m then .ok m
      else .error ["скомпилированный автомат не прошёл проверку wf/namesOK"]

end Figram.Engine

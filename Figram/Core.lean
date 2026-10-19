/-!
# Ядро: плоский автомат

Это всё, что исполняет движок. Кубики:

* **состояние** (`NodeId`) — сообщение, на котором стоит пользователь;
* **событие** (`Event`) — нажатие, текст, команда или срабатывание таймера;
* **стрелка** (`Row`) с упорядоченными вариантами (`FBranch`): первый вариант
  с выполненным условием выигрывает;
* **условие** — имя условия разработчика, его значение вычисляет хост;
* **действие** (`Action`).

Цель варианта — сообщение или «вернуться в раздел туда, где был»
(`Target.resume`): место читается из снимка во время шага.

Снимок пользователя (`Snapshot`) — текущее сообщение, запомненные места
разделов, активные таймеры с жетонами и счётчик шагов.

Шаг — чистая функция `Machine.step`. Эффекты только возвращаются хосту.
-/

namespace Figram

abbrev NodeId := String
abbrev EventId := String

/-! ## Таблицы «ключ → значение» -/

/-- Значение по ключу (первая запись). -/
def lookup {α β} [DecidableEq α] (t : List (α × β)) (k : α) : Option β :=
  (t.find? (fun p => decide (p.1 = k))).map (·.2)

/-- Записать значение: новая запись впереди, старые с тем же ключом удаляются. -/
def assign {α β} [DecidableEq α] (t : List (α × β)) (k : α) (v : β) : List (α × β) :=
  (k, v) :: t.filter (fun p => decide (p.1 ≠ k))

def erase {α β} [DecidableEq α] (t : List (α × β)) (k : α) : List (α × β) :=
  t.filter (fun p => decide (p.1 ≠ k))

/-! ## События и действия -/

def timerEvent (id : String) : EventId := "timer:" ++ id

inductive Event where
  | named (name : EventId)
  /-- Срабатывание таймера `id`, запущенного с жетоном `token`. -/
  | timer (id : String) (token : Nat)
  deriving DecidableEq, Repr

def Event.key : Event → EventId
  | .named n => n
  | .timer id _ => timerEvent id

inductive Action where
  /-- Отправить сообщение. -/
  | send (n : NodeId)
  /-- Вызвать код разработчика. -/
  | call (name : String)
  /-- Запустить таймер. Жетон назначает движок при исполнении шага. -/
  | startTimer (id : String) (ms : Nat) (token : Nat)
  | cancelTimer (id : String)
  /-- Служебное: запомнить, что в разделе `sec` пользователь на `n`.
  Хосту не выдаётся. -/
  | remember (sec : NodeId) (n : NodeId)
  deriving DecidableEq, Repr

/-! ## Снимок -/

structure Snapshot where
  /-- Текущее сообщение. -/
  state : NodeId
  /-- Запомненные места: раздел → сообщение. -/
  resume : List (NodeId × NodeId)
  /-- Активные таймеры: имя → жетон запуска. -/
  timers : List (String × Nat)
  /-- Номер последнего выполненного перехода. Растёт на 1 с каждым переходом;
  служит жетоном таймеров и основой ключей идемпотентности у хоста. -/
  seq : Nat
  deriving DecidableEq, Repr

def Snapshot.fresh : Snapshot := ⟨"", [], [], 0⟩

/-- Принимается ли событие: срабатывание таймера — только с текущим жетоном. -/
def Snapshot.accepts (s : Snapshot) : Event → Bool
  | .named _ => true
  | .timer id tok => decide (lookup s.timers id = some tok)

/-- Один шаг исполнения действий: служебные применяются к снимку,
остальные уходят хосту; таймерам назначается жетон. -/
def realizeOne (tok : Nat) (acc : Snapshot × List Action) : Action → Snapshot × List Action
  | .remember sec n => ({ acc.1 with resume := assign acc.1.resume sec n }, acc.2)
  | .startTimer id ms _ =>
    ({ acc.1 with timers := assign acc.1.timers id tok }, acc.2 ++ [.startTimer id ms tok])
  | .cancelTimer id => ({ acc.1 with timers := erase acc.1.timers id }, acc.2 ++ [.cancelTimer id])
  | a => (acc.1, acc.2 ++ [a])

/-- Переход в `T` с действиями `acts`: новый снимок и действия для хоста. -/
def realize (s : Snapshot) (T : NodeId) (acts : List Action) : Snapshot × List Action :=
  acts.foldl (realizeOne (s.seq + 1)) ({ s with state := T, seq := s.seq + 1 }, [])

/-! ## Автомат -/

/-- Значения условий разработчика, которые передаёт хост. -/
abbrev HostEnv := String → Bool

def guardOK (env : HostEnv) : Option String → Bool
  | none => true
  | some n => env n

inductive Target where
  | node (n : NodeId)
  /-- «Вернуться в раздел `sec` туда, где был». -/
  | resume (sec : NodeId)
  deriving DecidableEq, Repr

/-- Вариант стрелки: условие, цель, действия стрелки. -/
structure FBranch where
  guard : Option String
  target : Target
  actions : List Action
  deriving DecidableEq, Repr

structure Row where
  src : NodeId
  event : EventId
  branches : List FBranch
  deriving DecidableEq, Repr

/-- Как возвращаться в раздел: начальное сообщение и сообщения внутри. -/
structure ResumeSpec where
  sec : NodeId
  default : NodeId
  members : List NodeId
  deriving DecidableEq, Repr

structure Machine where
  states : List NodeId
  initial : NodeId
  initActions : List Action
  rows : List Row
  resumes : List ResumeSpec
  /-- Действия входа в сообщение: запомнить место, запустить таймеры, отправить. -/
  entry : List (NodeId × List Action)
  /-- Действия выхода из сообщения: отменить его таймеры. -/
  exit : List (NodeId × List Action)
  deriving Repr

namespace Machine

def row (m : Machine) (s : NodeId) (e : EventId) : Option Row :=
  m.rows.find? (fun r => decide (r.src = s ∧ r.event = e))

def resolve (m : Machine) (s : Snapshot) : Target → NodeId
  | .node n => n
  | .resume sec =>
    match m.resumes.find? (fun r => decide (r.sec = sec)) with
    | none => sec
    | some r =>
      match lookup s.resume sec with
      | some v => if v ∈ r.members then v else r.default
      | none => r.default

def entryOf (m : Machine) (n : NodeId) : List Action := (lookup m.entry n).getD []
def exitOf (m : Machine) (n : NodeId) : List Action := (lookup m.exit n).getD []

def fire (m : Machine) (s : Snapshot) (b : FBranch) : Snapshot × List Action :=
  let T := m.resolve s b.target
  realize s T (m.exitOf s.state ++ b.actions ++ m.entryOf T)

/-- Снимок указывает на сообщение, которого больше нет: начать бота заново,
отменив таймеры старого места. Запомненные места сохраняются. -/
def recover (m : Machine) (s : Snapshot) : Snapshot × List Action :=
  realize s m.initial (s.timers.map (fun p => .cancelTimer p.1) ++ m.initActions)

/-- **Шаг автомата.** -/
def step (m : Machine) (s : Snapshot) (ev : Event) (env : HostEnv) : Snapshot × List Action :=
  if s.state ∉ m.states then m.recover s
  else if !s.accepts ev then (s, [])
  else
    match m.row s.state ev.key with
    | none => (s, [])
    | some r =>
      match r.branches.find? (fun b => guardOK env b.guard) with
      | none => (s, [])
      | some b => m.fire s b

/-- Первый вход пользователя. -/
def start (m : Machine) : Snapshot × List Action := realize .fresh m.initial m.initActions

/-- Условия разработчика, которые нужны для шага из `st` по событию с ключом `e`. -/
def requiredGuards (m : Machine) (st : NodeId) (e : EventId) : List String :=
  match m.row st e with
  | none => []
  | some r => r.branches.filterMap (·.guard)

def targetOK (m : Machine) : Target → Bool
  | .node n => decide (n ∈ m.states)
  | .resume sec => m.resumes.any (fun r => decide (r.sec = sec))

/-- Проверка при экспорте: все цели существуют. -/
def wf (m : Machine) : Bool :=
  decide (m.initial ∈ m.states) &&
  m.rows.all (fun r => r.branches.all (fun b => m.targetOK b.target)) &&
  m.resumes.all (fun r => decide (r.default ∈ m.states) && r.members.all (fun v => decide (v ∈ m.states)))

end Machine
end Figram

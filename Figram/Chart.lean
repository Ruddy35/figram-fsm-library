import Figram.Core

/-!
# Уровень дизайнера: база и три упрощения

То, что рисует дизайнер. База — те же пять кубиков, что в ядре:
сообщение, событие, стрелка с вариантами (условия пишутся текстом),
переменная, действие. Поверх — три упрощения:

1. **Разделы.** Сообщения можно класть в разделы, разделы — друг в друга.
   Стрелка, нарисованная от раздела, работает на любом сообщении внутри
   («Отмена» один раз на весь заказ, `/start` на разделе вокруг всего бота).
   Стрелка в раздел ведёт на его начальное сообщение.
2. **«Вернуться туда, где был».** Стрелка может вести не в сообщение,
   а «в раздел туда, где пользователь остановился».
3. **Напоминание по времени.** Таймер на сообщении: запускается, когда
   пользователь пришёл, отменяется, когда ушёл; срабатывание приходит
   событием `timerEvent id`.

`Chart.step` — **эталонная семантика** схемы. Компилятор (`Figram.Compile`)
разворачивает упрощения в базу, теорема `compile_correct` доказывает,
что поведение совпадает.

## Решения спецификации

1. Всплытие: варианты собираются от сообщения к разделам выше; первый
   вариант с истинным условием выигрывает. Если у сообщения есть стрелка,
   но все её условия ложны, событие уходит к разделу выше.
2. Порядок действий: выход (отмена таймеров сообщения) → действия стрелки →
   вход (запоминание места, запуск таймеров, отправка сообщения).
3. Стрелка на то же сообщение отправляет его заново и перезапускает таймеры.
4. Место запоминается при входе в сообщение: для каждого раздела над ним,
   в который ведёт хотя бы одна стрелка «вернуться туда, где был».
   Для стрелки снаружи раздела это то же самое, что запомнить место при выходе;
   изнутри раздела такая стрелка ведёт на текущее сообщение.
5. Если запомненного сообщения больше нет в схеме (новая версия),
   «вернуться туда, где был» ведёт на начальное сообщение раздела.
6. Событие без подходящей стрелки игнорируется.
7. Срабатывание таймера принимается только с жетоном текущего запуска.
8. Если сообщения из снимка больше нет в схеме, пользователь начинает бота
   заново; таймеры старого места отменяются, запомненные места сохраняются.
-/

namespace Figram

inductive NodeKind where
  | message
  /-- Раздел и его начальное сообщение (или вложенный раздел). -/
  | sec (initial : NodeId)
  deriving DecidableEq, Repr

structure Node where
  id : NodeId
  parent : Option NodeId
  kind : NodeKind
  deriving DecidableEq, Repr

/-- Цель стрелки в схеме дизайнера. -/
inductive Dest where
  /-- Сообщение или раздел (тогда — его начальное сообщение). -/
  | node (n : NodeId)
  /-- «Вернуться в раздел туда, где был». -/
  | history (sec : NodeId)
  deriving DecidableEq, Repr

structure Branch where
  /-- Имя условия, написанного текстом; нет — «иначе / всегда». -/
  guard : Option String
  target : Dest
  actions : List Action
  deriving DecidableEq, Repr

structure Transition where
  /-- Сообщение или раздел. -/
  src : NodeId
  event : EventId
  branches : List Branch
  deriving DecidableEq, Repr

/-- Напоминание на сообщении. -/
structure Timer where
  id : String
  node : NodeId
  delayMs : Nat
  deriving DecidableEq, Repr

structure Chart where
  nodes : List Node
  /-- Куда попадает пользователь при первом входе. -/
  root : NodeId
  transitions : List Transition
  timers : List Timer
  deriving Repr

namespace Chart

def find (c : Chart) (n : NodeId) : Option Node :=
  c.nodes.find? (fun x => decide (x.id = n))

def parent (c : Chart) (n : NodeId) : Option NodeId :=
  (c.find n).bind (·.parent)

def ancAux (c : Chart) : Nat → NodeId → List NodeId
  | 0, n => [n]
  | k + 1, n => n :: match c.parent n with
    | none => []
    | some p => c.ancAux k p

/-- Само сообщение и все разделы над ним, снизу вверх. -/
def anc (c : Chart) (n : NodeId) : List NodeId :=
  c.ancAux c.nodes.length n

def enterAux (c : Chart) : Nat → NodeId → NodeId
  | 0, n => n
  | k + 1, n => match c.find n with
    | some ⟨_, _, .sec i⟩ => c.enterAux k i
    | _ => n

/-- Вход в узел: для раздела — спуск по начальным сообщениям. -/
def enter (c : Chart) (n : NodeId) : NodeId :=
  c.enterAux c.nodes.length n

def leafIds (c : Chart) : List NodeId :=
  (c.nodes.filter (fun x => decide (x.kind = .message))).map (·.id)

/-- Разделы, в которые ведёт хотя бы одна стрелка «вернуться туда, где был». -/
def histSecs (c : Chart) : List NodeId :=
  c.transitions.flatMap fun t => t.branches.filterMap fun b =>
    match b.target with
    | .history s => some s
    | .node _ => none

/-- Сообщения внутри раздела `s` на любой глубине. -/
def histCands (c : Chart) (s : NodeId) : List NodeId :=
  c.leafIds.filter (fun l => decide (s ∈ c.anc l ∧ l ≠ s))

def timersOf (c : Chart) (n : NodeId) : List Timer :=
  c.timers.filter (fun t => decide (t.node = n))

/-- Действия входа в сообщение `T`: запомнить место во всех разделах над ним,
куда ведёт «вернуться туда, где был»; запустить таймеры; отправить сообщение. -/
def entryActs (c : Chart) (T : NodeId) : List Action :=
  (c.anc T).filterMap (fun s =>
      if s ∈ c.histSecs ∧ s ≠ T then some (.remember s T) else none)
  ++ (c.timersOf T).map (fun t => .startTimer t.id t.delayMs 0)
  ++ [.send T]

/-- Действия выхода из сообщения `L`: отменить его таймеры. -/
def exitActs (c : Chart) (L : NodeId) : List Action :=
  (c.timersOf L).map (fun t => .cancelTimer t.id)

/-- Куда ведёт «вернуться туда, где был». -/
def restoreTarget (c : Chart) (s : Snapshot) (sec : NodeId) : NodeId :=
  match lookup s.resume sec with
  | some v => if v ∈ c.histCands sec then v else c.enter sec
  | none => c.enter sec

def resolve (c : Chart) (s : Snapshot) : Dest → NodeId
  | .node n => c.enter n
  | .history sec => c.restoreTarget s sec

/-- Варианты стрелок узла `a` по событию `e`. -/
def branchesAt (c : Chart) (a : NodeId) (e : EventId) : List Branch :=
  (c.transitions.filter (fun t => decide (t.src = a ∧ t.event = e))).flatMap (·.branches)

/-- Все кандидаты для сообщения `L`: сначала его собственные, затем
разделов выше. Это и есть «всплытие». -/
def cands (c : Chart) (L : NodeId) (e : EventId) : List Branch :=
  (c.anc L).flatMap (c.branchesAt · e)

def fire (c : Chart) (s : Snapshot) (b : Branch) : Snapshot × List Action :=
  let T := c.resolve s b.target
  realize s T (c.exitActs s.state ++ b.actions ++ c.entryActs T)

def initActs (c : Chart) : List Action := c.entryActs (c.enter c.root)

def recover (c : Chart) (s : Snapshot) : Snapshot × List Action :=
  realize s (c.enter c.root) (s.timers.map (fun p => .cancelTimer p.1) ++ c.initActs)

/-- **Эталонная семантика схемы дизайнера.** -/
def step (c : Chart) (s : Snapshot) (ev : Event) (env : HostEnv) : Snapshot × List Action :=
  if s.state ∉ c.leafIds then c.recover s
  else if !s.accepts ev then (s, [])
  else
    match (c.cands s.state ev.key).find? (fun b => guardOK env b.guard) with
    | none => (s, [])
    | some b => c.fire s b

def start (c : Chart) : Snapshot × List Action := realize .fresh (c.enter c.root) c.initActs

end Chart
end Figram

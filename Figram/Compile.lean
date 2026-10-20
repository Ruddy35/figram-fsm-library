import Figram.Chart

/-!
# Компилятор: схема дизайнера → плоский автомат

* разделы и всплытие → у каждого сообщения своя стрелка, в которую по порядку
  сложены варианты сообщения и всех разделов над ним;
* вход в раздел → цель заранее заменена на его начальное сообщение;
* «вернуться туда, где был» → операция ядра `Target.resume` плюс одна запись
  `ResumeSpec` на раздел (размер растёт линейно, а не квадратично);
* вход и выход сообщения → таблицы `entry` и `exit`, по записи на сообщение.

Спецификация — `compileSpec`: простые списковые определения (`cands`,
`compileRow`, `histCands`), по ним доказана главная теорема. Исполняется
`compile`: те же данные, но с индексами (`Figram.Index`) — предки каждого
сообщения считаются один раз, для сообщения перебираются только стрелки
его предков, места «вернуться» собираются за один проход. Равенство
`compile_eq_spec : c.compile = c.compileSpec` доказано в `Figram.Correct`.
-/

namespace Figram

namespace Chart

def toF (c : Chart) (b : Branch) : FBranch :=
  ⟨b.guard, (match b.target with
    | .node n => .node (c.enter n)
    | .history s => .resume s), b.actions⟩

def events (c : Chart) : List EventId :=
  dedup (c.transitions.map (·.event))

def compileRow (c : Chart) (L : NodeId) (e : EventId) : Row :=
  ⟨L, e, (c.cands L e).map c.toF⟩

def mkResume (c : Chart) (s : NodeId) : ResumeSpec := ⟨s, c.enter s, c.histCands s⟩

/-- Стрелки сообщения `L` по спецификации: по всем событиям документа
в порядке `events`, только непустые. -/
def rowsSpec (c : Chart) (L : NodeId) : List Row :=
  (c.events.map (c.compileRow L)).filter (fun r => !r.branches.isEmpty)

/-- **Спецификация компилятора.** -/
def compileSpec (c : Chart) : Machine where
  states := c.leafIds
  initial := c.enter c.root
  initActions := c.initActs
  table := c.leafIds.map fun L => (L, c.rowsSpec L)
  resumes := (dedup c.histSecs).map c.mkResume
  entry := c.leafIds.map fun L => (L, c.entryActs L)
  exit := c.leafIds.map fun L => (L, c.exitActs L)

/-! ## Быстрая реализация -/

/-- Индексы схемы: узел по имени (первое объявление, как `find`), разделы
с «вернуться туда, где был», таймеры по сообщениям. -/
structure Index where
  fuel : Nat
  node : Std.HashMap NodeId Node
  hist : Std.HashSet NodeId
  timers : Std.HashMap NodeId (List Timer)

def index (c : Chart) : Index where
  fuel := c.nodes.length
  node := firstIndex (c.nodes.map fun x => (x.id, x))
  hist := keySet c.histSecs
  timers := concatIndex (c.timers.map fun t => (t.node, [t]))

namespace Index

def parent (ix : Index) (n : NodeId) : Option NodeId := (ix.node[n]?).bind (·.parent)

def ancAux (ix : Index) : Nat → NodeId → List NodeId
  | 0, n => [n]
  | k + 1, n => n :: match ix.parent n with
    | none => []
    | some p => ix.ancAux k p

def anc (ix : Index) (n : NodeId) : List NodeId := ix.ancAux ix.fuel n

def enterAux (ix : Index) : Nat → NodeId → NodeId
  | 0, n => n
  | k + 1, n => match ix.node[n]? with
    | some ⟨_, _, .sec i⟩ => ix.enterAux k i
    | _ => n

def enter (ix : Index) (n : NodeId) : NodeId := ix.enterAux ix.fuel n

def toF (ix : Index) (b : Branch) : FBranch :=
  ⟨b.guard, (match b.target with
    | .node n => .node (ix.enter n)
    | .history s => .resume s), b.actions⟩

def timersOf (ix : Index) (n : NodeId) : List Timer := (ix.timers[n]?).getD []

def entryActs (ix : Index) (T : NodeId) : List Action :=
  (ix.anc T).filterMap (fun s =>
      if (ix.hist.contains s && s != T) = true then some (.remember s T) else none)
  ++ (ix.timersOf T).map (fun t => .startTimer t.id t.delayMs 0)
  ++ [.send T]

def exitActs (ix : Index) (L : NodeId) : List Action :=
  (ix.timersOf L).map (fun t => .cancelTimer t.id)

/-- Цель стрелки ведёт в сообщение из `leaves` («вернуться» проверяется отдельно). -/
def destIn (ix : Index) (leaves : Std.HashSet NodeId) : Dest → Bool
  | .node n => leaves.contains (ix.enter n)
  | .history _ => true

end Index

/-- Стрелки каждого узла с уже переведёнными вариантами:
узел → список (событие, варианты) в порядке стрелок. -/
def transIndex (c : Chart) (ix : Index) : Std.HashMap NodeId (List (EventId × List FBranch)) :=
  concatIndex (c.transitions.map fun t => (t.src, [(t.event, t.branches.map ix.toF)]))

/-- Стрелки сообщения `L`: варианты всех его предков, сложенные по событиям,
в порядке событий документа `evs`. -/
def leafRows (evs : List EventId) (tr : Std.HashMap NodeId (List (EventId × List FBranch)))
    (ix : Index) (L : NodeId) : List Row :=
  let tab := concatIndex ((ix.anc L).flatMap fun a => (tr[a]?).getD [])
  evs.filterMap fun e =>
    let bs := (tab[e]?).getD []
    if bs.isEmpty then none else some ⟨L, e, bs⟩

/-- Сообщения внутри разделов с «вернуться туда, где был», за один проход. -/
def memberIndex (c : Chart) (ix : Index) : Std.HashMap NodeId (List NodeId) :=
  concatIndex (c.leafIds.flatMap fun l =>
    (dedupFast ((ix.anc l).filter fun a => ix.hist.contains a && a != l)).map fun a => (a, [l]))

/-- **Компилятор** (быстрая реализация `compileSpec`). -/
def compile (c : Chart) : Machine :=
  let ix := c.index
  let tr := c.transIndex ix
  let evs := dedupFast (c.transitions.map (·.event))
  let mem := c.memberIndex ix
  let leaves := c.leafIds
  { states := leaves
    initial := ix.enter c.root
    initActions := ix.entryActs (ix.enter c.root)
    table := leaves.map fun L => (L, leafRows evs tr ix L)
    resumes := (dedupFast c.histSecs).map fun s => ⟨s, ix.enter s, (mem[s]?).getD []⟩
    entry := leaves.map fun L => (L, ix.entryActs L)
    exit := leaves.map fun L => (L, ix.exitActs L) }

/-- Проверка `compile.wf` без построения автомата (`wfDirect_eq` в
`Figram.Correct`): начальное сообщение, цели стрелок предков сообщений и
начальные сообщения разделов с «вернуться» — сообщения. -/
def wfDirect (c : Chart) : Bool :=
  let ix := c.index
  let leaves := keySet c.leafIds
  let ancs := keySet (c.leafIds.flatMap ix.anc)
  leaves.contains (ix.enter c.root)
  && c.transitions.all (fun t =>
      !ancs.contains t.src || t.branches.all (fun b => ix.destIn leaves b.target))
  && c.histSecs.all (fun s => leaves.contains (ix.enter s))

end Chart
end Figram

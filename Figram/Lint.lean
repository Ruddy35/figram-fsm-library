import Figram.Compile

/-!
# Проверка схемы перед экспортом

Ошибки, которые редактор показывает дизайнеру. Решающая проверка — итоговая:
`wfDirect`, она равна `c.compile.wf` (`wfDirect_eq`), то есть условию теорем
`Figram.Run`; отсюда `lint_nil_wf` (`Figram.LintProofs`). Остальные проверки ловят ошибки раньше
и объясняют их понятным языком. Проверка не строит автомат: документ
компилируется один раз — при загрузке.

Предупреждения (`lintWarnings`) — то, что корректно, но никогда не сработает
или ведёт себя неожиданно. На результат проверки они не влияют.
-/

namespace Figram
namespace Chart

def exists_ (c : Chart) (n : NodeId) : Bool := (c.find n).isSome

def isSec (c : Chart) (n : NodeId) : Bool :=
  match c.find n with
  | some ⟨_, _, .sec _⟩ => true
  | _ => false

/-- Имя узла в сообщениях: корень документа (пустое имя) называется «bot». -/
def shown (n : NodeId) : String := if n.isEmpty then "bot" else n

def lintMsg : String := "Схема не проходит итоговую проверку: какая-то стрелка ведёт не в сообщение"

/-- Ошибки схемы, кроме итоговой проверки `wfDirect`. Поиск узлов — по индексу
(`Chart.index`: то же, что `find`), поэтому проверка линейна. -/
def lintCore (c : Chart) : List String := Id.run do
  let ix := c.index
  let found (n : NodeId) : Bool := (ix.node.find? n).isSome
  let sec (n : NodeId) : Bool := match ix.node.find? n with
    | some ⟨_, _, .sec _⟩ => true
    | _ => false
  -- позиции объявлений по имени: повтор сообщается один раз, у первого объявления
  let pos := concatIndex (c.nodes.zipIdx.map fun (n, i) => (n.id, [i]))
  let mut errs : Array String := #[]
  for (n, i) in c.nodes.zipIdx do
    let ps := pos.concat n.id
    if ps.length > 1 && ps.head? == some i then
      errs := errs.push s!"Узел «{shown n.id}» объявлен несколько раз"
    match n.parent with
    | some p =>
      if !found p then errs := errs.push s!"У «{shown n.id}» несуществующий раздел-родитель «{shown p}»"
      else if !sec p then errs := errs.push s!"Родитель «{shown p}» узла «{shown n.id}» — не раздел"
    | none => pure ()
    -- цикл: подъём по родителям упирается в лимит
    if (ix.anc n.id).length > ix.fuel then
      errs := errs.push s!"Цикл во вложенности разделов через «{shown n.id}»"
    match n.kind with
    | .sec i =>
      if (ix.node.find? i).bind (·.parent) != some n.id then
        errs := errs.push
          s!"Начальный экран «{shown i}» раздела «{shown n.id}» должен лежать внутри него"
    | .message => pure ()
  if !found c.root then errs := errs.push s!"Стартовый узел «{shown c.root}» не существует"
  for t in c.transitions do
    if !found t.src then
      errs := errs.push s!"Стрелка по «{t.event}» начинается в несуществующем «{shown t.src}»"
    for b in t.branches do
      match b.target with
      | .node n =>
        if !found n then errs := errs.push s!"Стрелка «{shown t.src}» → «{shown n}»: цели не существует"
      | .history s =>
        if !sec s then
          errs := errs.push
            s!"Стрелка «{shown t.src}» → «вернуться туда, где был» в «{shown s}»: это не раздел"
  let leaves := keySet c.leafIds
  for tm in c.timers do
    if !leaves.contains tm.node then
      errs := errs.push s!"Таймер «{tm.id}» висит не на сообщении"
  return errs.toList

/-- Ошибки схемы. Итоговая проверка — `wfDirect` (= `c.compile.wf`). -/
def lint (c : Chart) : List String :=
  let errs := c.lintCore
  if errs.isEmpty && !c.wfDirect then errs ++ [lintMsg] else errs

/-! ## Предупреждения -/

/-- Таймер `X` события `timer:X`. -/
def timerOfEvent (e : EventId) : Option String :=
  if e.startsWith "timer:" then some (e.drop 6).toString else none

/-- Предупреждения о схеме, которая прошла проверку: мёртвые варианты,
недостижимые сообщения, стрелки, которые никогда не сработают, и петли
таймеров, которые не дают сработать соседним таймерам. -/
def lintWarnings (c : Chart) : List String := Id.run do
  let ix := c.index
  let m := c.compile
  let mut ws : Array String := #[]
  -- 1. Варианты одной стрелки после варианта без условия и повтор условия.
  for t in c.transitions do
    let mut uncond : Option Nat := none
    let mut seen : List String := []
    let mut k := 0
    for b in t.branches do
      k := k + 1
      match uncond with
      | some u =>
        ws := ws.push (s!"Стрелка «{shown t.src}» по «{t.event}»: вариант {k} никогда не сработает — " ++
          s!"перед ним вариант {u} без условия")
      | none =>
        match b.guard with
        | none => uncond := some k
        | some g =>
          if seen.contains g then
            ws := ws.push (s!"Стрелка «{shown t.src}» по «{t.event}»: условие «{g}» повторяется, " ++
              s!"вариант {k} никогда не сработает")
          else seen := g :: seen
  -- 2. Стрелки `timer:X` там, где таймер X не запускается: сообщение без X
  --    или раздел, внутри которого нет сообщения с X.
  let owners := concatIndex (c.timers.map fun tm => (tm.id, [tm.node]))
  for t in c.transitions do
    match timerOfEvent t.event with
    | none => pure ()
    | some x =>
      let os := owners.concat x
      unless os.any (fun o => (ix.anc o).contains t.src) do
        ws := ws.push (s!"Стрелка «{t.event}» на «{shown t.src}» никогда не сработает: таймер «{x}» " ++
          "запускают только другие сообщения, а срабатывание принимает лишь сообщение, которое его запустило")
  -- 3. «Вернуться туда, где был», нарисованное только изнутри раздела.
  for s in dedupFast c.histSecs do
    let srcs := c.transitions.filter fun t => t.branches.any fun b => b.target == .history s
    if srcs.all (fun t => (ix.anc t.src).contains s) then
      ws := ws.push (s!"«Вернуться туда, где был» в «{shown s}» нарисовано только изнутри раздела: " ++
        "такая стрелка всегда оставляет пользователя на текущем сообщении")
  -- 4. Достижимость сообщений от начала бота по стрелкам, которые могут сработать.
  let tab := firstIndex m.table
  let owns (L : NodeId) (e : EventId) : Bool := match timerOfEvent e with
    | none => true
    | some x => (m.exitOf L).contains (.cancelTimer x)
  let target (b : FBranch) : NodeId := match b.target with
    | .node n => n
    | .resume sec => ((m.resumes.find? (·.sec == sec)).map (·.default)).getD sec
  let mut seen : Tab Unit := (Tab.empty m.states.length).insert m.initial ()
  let mut queue : Array NodeId := #[m.initial]
  let mut i := 0
  for _ in [0:m.states.length + 1] do
    if h : i < queue.size then
      let L := queue[i]
      i := i + 1
      for r in (tab.find? L).getD [] do
        if owns L r.event then
          -- варианты после безусловного и с уже встреченным условием не сработают
          let mut gs : List String := []
          for b in untilUnguarded (·.guard) r.branches do
            let dead := match b.guard with
              | some g => gs.contains g
              | none => false
            if let some g := b.guard then gs := g :: gs
            let T := target b
            unless dead || seen.contains T do
              seen := seen.insert T ()
              queue := queue.push T
  for L in m.states do
    unless seen.contains L do
      ws := ws.push s!"Сообщение «{shown L}» недостижимо: от начала бота к нему не ведёт ни одна стрелка"
  -- 5. Петля по таймеру на своё сообщение перезапускает и соседние таймеры.
  for L in m.states do
    let mine := ix.timersOf L
    for tm in mine do
      match m.row L (timerEvent tm.id) with
      | none => pure ()
      | some r =>
        if (untilUnguarded (·.guard) r.branches).any (fun b => b.target == .node L) then
          let starved := mine.filter fun o => o.id != tm.id && o.delayMs ≥ tm.delayMs
          unless starved.isEmpty do
            ws := ws.push (s!"Петля «{timerEvent tm.id}» на «{shown L}» перезапускает таймеры сообщения " ++
              s!"({", ".intercalate (starved.map fun o => s!"«{o.id}»")}): пока срабатывает «{tm.id}», " ++
              "они не сработают")
  return ws.toList

end Chart
end Figram

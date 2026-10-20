import Figram.Correct

/-!
# Проверка схемы перед экспортом

Ошибки, которые редактор показывает дизайнеру. Решающая проверка — итоговая:
`wfDirect`, она равна `c.compile.wf` (`wfDirect_eq`), то есть условию теорем
`Figram.Run`; отсюда `lint_nil_wf`. Остальные проверки ловят ошибки раньше
и объясняют их понятным языком. Проверка не строит автомат: документ
компилируется один раз — при загрузке.
-/

namespace Figram
namespace Chart

def exists_ (c : Chart) (n : NodeId) : Bool := (c.find n).isSome

def isSec (c : Chart) (n : NodeId) : Bool :=
  match c.find n with
  | some ⟨_, _, .sec _⟩ => true
  | _ => false

def lintMsg : String := "Схема не проходит итоговую проверку: какая-то стрелка ведёт не в сообщение"

/-- Ошибки схемы, кроме итоговой проверки `wfDirect`. Поиск узлов — по индексу
(`Chart.index`: то же, что `find`), поэтому проверка линейна. -/
def lintCore (c : Chart) : List String := Id.run do
  let ix := c.index
  let found (n : NodeId) : Bool := (ix.node[n]?).isSome
  let sec (n : NodeId) : Bool := match ix.node[n]? with
    | some ⟨_, _, .sec _⟩ => true
    | _ => false
  let count : Std.HashMap NodeId Nat :=
    c.nodes.foldl (fun m n => m.insert n.id (m.getD n.id 0 + 1)) {}
  let mut errs : Array String := #[]
  for n in c.nodes do
    if count.getD n.id 0 > 1 then
      errs := errs.push s!"Узел «{n.id}» объявлен несколько раз"
    match n.parent with
    | some p =>
      if !found p then errs := errs.push s!"У «{n.id}» несуществующий раздел-родитель «{p}»"
      else if !sec p then errs := errs.push s!"Родитель «{p}» узла «{n.id}» — не раздел"
    | none => pure ()
    -- цикл: подъём по родителям упирается в лимит
    if (ix.anc n.id).length > c.nodes.length then
      errs := errs.push s!"Цикл во вложенности разделов через «{n.id}»"
    match n.kind with
    | .sec i =>
      if (ix.node[i]?).bind (·.parent) != some n.id then
        errs := errs.push
          s!"Начальный экран «{i}» раздела «{n.id}» должен лежать внутри него"
    | .message => pure ()
  if !found c.root then errs := errs.push s!"Стартовый узел «{c.root}» не существует"
  for t in c.transitions do
    if !found t.src then
      errs := errs.push s!"Стрелка по «{t.event}» начинается в несуществующем «{t.src}»"
    for b in t.branches do
      match b.target with
      | .node n =>
        if !found n then errs := errs.push s!"Стрелка «{t.src}» → «{n}»: цели не существует"
      | .history s =>
        if !sec s then
          errs := errs.push
            s!"Стрелка «{t.src}» → «вернуться туда, где был» в «{s}»: это не раздел"
  let leaves := keySet c.leafIds
  for tm in c.timers do
    if !leaves.contains tm.node then
      errs := errs.push s!"Таймер «{tm.id}» висит не на сообщении"
  return errs.toList

/-- Ошибки схемы. Итоговая проверка — `wfDirect` (= `c.compile.wf`). -/
def lint (c : Chart) : List String :=
  let errs := c.lintCore
  if errs.isEmpty && !c.wfDirect then errs ++ [lintMsg] else errs

/-- **Схема без ошибок компилируется в автомат, прошедший `wf`.** -/
theorem lint_nil_wf (c : Chart) (h : c.lint = []) : c.compile.wf = true := by
  rw [wfDirect_eq]
  unfold lint at h
  cases hw : c.wfDirect with
  | true => rfl
  | false =>
    exfalso
    simp only [hw, Bool.not_false, Bool.and_true] at h
    split at h
    · simp at h
    · rename_i hc
      rw [h] at hc
      simp at hc

end Chart
end Figram

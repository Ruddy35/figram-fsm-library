import Figram.Compile

/-!
# Проверка схемы перед экспортом

Ошибки, которые редактор показывает дизайнеру. Решающая проверка —
`c.compile.wf`: именно она входит в условия теорем `Figram.Run`.
Остальные проверки ловят ошибки раньше и объясняют их понятным языком.
-/

namespace Figram
namespace Chart

def exists_ (c : Chart) (n : NodeId) : Bool := (c.find n).isSome

def isSec (c : Chart) (n : NodeId) : Bool :=
  match c.find n with
  | some ⟨_, _, .sec _⟩ => true
  | _ => false

def lint (c : Chart) : List String := Id.run do
  let mut errs : List String := []
  let ids := c.nodes.map (·.id)
  for n in c.nodes do
    if (ids.filter (· = n.id)).length > 1 then
      errs := errs ++ [s!"Узел «{n.id}» объявлен несколько раз"]
    match n.parent with
    | some p =>
      if !c.exists_ p then errs := errs ++ [s!"У «{n.id}» несуществующий раздел-родитель «{p}»"]
      else if !c.isSec p then errs := errs ++ [s!"Родитель «{p}» узла «{n.id}» — не раздел"]
    | none => pure ()
    -- цикл: подъём по родителям упирается в лимит
    if (c.anc n.id).length > c.nodes.length then
      errs := errs ++ [s!"Цикл во вложенности разделов через «{n.id}»"]
    match n.kind with
    | .sec i =>
      if (c.find i).bind (·.parent) != some n.id then
        errs := errs ++ [s!"Начальный экран «{i}» раздела «{n.id}» должен лежать внутри него"]
    | .message => pure ()
  if !c.exists_ c.root then errs := errs ++ [s!"Стартовый узел «{c.root}» не существует"]
  for t in c.transitions do
    if !c.exists_ t.src then
      errs := errs ++ [s!"Стрелка по «{t.event}» начинается в несуществующем «{t.src}»"]
    for b in t.branches do
      match b.target with
      | .node n =>
        if !c.exists_ n then errs := errs ++ [s!"Стрелка «{t.src}» → «{n}»: цели не существует"]
      | .history s =>
        if !c.isSec s then
          errs := errs ++ [s!"Стрелка «{t.src}» → «вернуться туда, где был» в «{s}»: это не раздел"]
  for tm in c.timers do
    if !c.leafIds.contains tm.node then
      errs := errs ++ [s!"Таймер «{tm.id}» висит не на сообщении"]
  if errs.isEmpty && !c.compile.wf then
    errs := errs ++ ["Схема не проходит итоговую проверку: какая-то стрелка ведёт не в сообщение"]
  return errs

end Chart
end Figram

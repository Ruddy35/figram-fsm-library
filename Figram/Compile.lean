import Figram.Chart

/-!
# Компилятор: схема дизайнера → плоский автомат

* разделы и всплытие → у каждого сообщения своя стрелка, в которую по порядку
  сложены варианты сообщения и всех разделов над ним;
* вход в раздел → цель заранее заменена на его начальное сообщение;
* «вернуться туда, где был» → операция ядра `Target.resume` плюс одна запись
  `ResumeSpec` на раздел (размер растёт линейно, а не квадратично);
* вход и выход сообщения → таблицы `entry` и `exit`, по записи на сообщение.
-/

namespace Figram

/-- Удаление повторов с сохранением порядка. -/
def dedup {α} [DecidableEq α] : List α → List α
  | [] => []
  | x :: xs => if x ∈ dedup xs then dedup xs else x :: dedup xs

theorem mem_dedup {α} [DecidableEq α] (a : α) (l : List α) :
    a ∈ dedup l ↔ a ∈ l := by
  induction l with
  | nil => simp [dedup]
  | cons x xs ih =>
    simp only [dedup]
    split
    · rename_i h
      constructor
      · intro ha; exact List.mem_cons_of_mem _ (ih.mp ha)
      · intro ha
        rcases List.mem_cons.mp ha with rfl | ha
        · exact h
        · exact ih.mpr ha
    · simp [ih]

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

def compile (c : Chart) : Machine where
  states := c.leafIds
  initial := c.enter c.root
  initActions := c.initActs
  rows := c.leafIds.flatMap fun L =>
    (c.events.map (c.compileRow L)).filter (fun r => !r.branches.isEmpty)
  resumes := (dedup c.histSecs).map c.mkResume
  entry := c.leafIds.map fun L => (L, c.entryActs L)
  exit := c.leafIds.map fun L => (L, c.exitActs L)

end Chart
end Figram

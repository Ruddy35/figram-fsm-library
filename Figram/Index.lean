import Std.Data.HashMap
import Std.Data.HashSet

/-!
# Индексы для быстрой компиляции

Компилятор и загрузка строят хеш-таблицы один раз на документ. Каждая
таблица здесь — быстрая реализация спискового определения, и равенство
доказано: `keySet_contains`, `firstIndex_get`, `concatIndex_get`,
`dedupFast_eq`. Поэтому все остальные доказательства говорят о простых
списковых определениях (спецификации), а исполняется быстрый код.
-/

namespace Figram

open Std

/-- Удаление повторов с сохранением порядка (остаётся последнее вхождение). -/
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

theorem nodup_dedup {α} [DecidableEq α] (l : List α) : (dedup l).Nodup := by
  induction l with
  | nil => simp [dedup]
  | cons x xs ih =>
    simp only [dedup]
    split
    · exact ih
    · rename_i h
      exact List.nodup_cons.mpr ⟨h, ih⟩

/-- Множество строк списка. -/
def keySet (l : List String) : HashSet String := l.foldr (fun k s => s.insert k) {}

theorem keySet_contains (l : List String) (k : String) :
    (keySet l).contains k = decide (k ∈ l) := by
  induction l with
  | nil => simp [keySet]
  | cons x xs ih =>
    simp only [keySet, List.foldr_cons] at ih ⊢
    rw [HashSet.contains_insert, ih]
    by_cases h : x = k
    · subst h; simp
    · have : (x == k) = false := by simpa using h
      simp [this, List.mem_cons, Ne.symm h]

/-- Первая запись с ключом — то же, что поиск по списку пар. -/
def firstIndex {β} (l : List (String × β)) : HashMap String β :=
  l.foldr (fun p m => m.insert p.1 p.2) {}

theorem firstIndex_get {β} (l : List (String × β)) (k : String) :
    (firstIndex l)[k]? = (l.find? (fun p => decide (p.1 = k))).map (·.2) := by
  induction l with
  | nil => simp [firstIndex]
  | cons p ps ih =>
    simp only [firstIndex, List.foldr_cons] at ih ⊢
    rw [HashMap.getElem?_insert, ih, List.find?_cons]
    by_cases h : p.1 = k
    · simp [h]
    · have : (p.1 == k) = false := by simpa using h
      simp [this, h]

/-- Шаг `concatIndex`: список пары ставится перед уже собранным. Пустой
собранный список не копирует список пары (общие списки остаются общими). -/
def concatStep {β} (p : String × List β) (m : HashMap String (List β)) : HashMap String (List β) :=
  match m[p.1]? with
  | none => m.insert p.1 p.2
  | some old => m.insert p.1 (p.2 ++ old)

/-- Сцепление списков по ключу с сохранением порядка: для ключа `k` —
списки всех пар с ключом `k`, подряд в порядке пар. -/
def concatIndex {β} (l : List (String × List β)) : HashMap String (List β) :=
  l.foldr concatStep {}

theorem concatStep_get {β} (p : String × List β) (m : HashMap String (List β)) (k : String) :
    ((concatStep p m)[k]?).getD [] =
      (if p.1 = k then p.2 else []) ++ (m[k]?).getD [] := by
  unfold concatStep
  by_cases h : p.1 = k
  · subst h
    split <;> rename_i hm <;> simp [hm]
  · have : (p.1 == k) = false := by simpa using h
    split <;> simp [HashMap.getElem?_insert, this, h]

theorem concatIndex_get {β} (l : List (String × List β)) (k : String) :
    ((concatIndex l)[k]?).getD [] = (l.filter (fun p => decide (p.1 = k))).flatMap (·.2) := by
  induction l with
  | nil => simp [concatIndex]
  | cons p ps ih =>
    simp only [concatIndex, List.foldr_cons] at ih ⊢
    rw [concatStep_get, ih, List.filter_cons]
    by_cases h : p.1 = k <;> simp [h]

/-- Шаг `dedupFast`. -/
def dedupStep (x : String) (p : HashSet String × List String) : HashSet String × List String :=
  if p.1.contains x then p else (p.1.insert x, x :: p.2)

/-- Удаление повторов за линейное время; равно `dedup` (`dedupFast_eq`). -/
def dedupFast (l : List String) : List String := (l.foldr dedupStep ({}, [])).2

theorem dedupFast_aux (l : List String) :
    (∀ k, (l.foldr dedupStep ({}, [])).1.contains k = decide (k ∈ l)) ∧
    (l.foldr dedupStep ({}, [])).2 = dedup l := by
  induction l with
  | nil => simp [dedup]
  | cons x xs ih =>
    obtain ⟨ih1, ih2⟩ := ih
    simp only [List.foldr_cons]
    generalize xs.foldr dedupStep ({}, []) = q at ih1 ih2 ⊢
    unfold dedupStep
    by_cases hx : x ∈ xs
    · have hc : q.1.contains x = true := by rw [ih1]; simpa using hx
      have hd : x ∈ dedup xs := (mem_dedup x xs).mpr hx
      simp only [hc, ite_true, dedup, hd]
      refine ⟨fun k => ?_, ih2⟩
      rw [ih1]
      by_cases hk : k = x
      · subst hk; simp [hx]
      · simp [List.mem_cons, hk]
    · have hc : q.1.contains x = false := by rw [ih1]; simpa using hx
      have hd : x ∉ dedup xs := fun h => hx ((mem_dedup x xs).mp h)
      simp only [hc, Bool.false_eq_true, ite_false, dedup, hd, ih2, and_true]
      intro k
      rw [HashSet.contains_insert, ih1]
      by_cases hk : x = k
      · subst hk; simp
      · have : (x == k) = false := by simpa using hk
        simp [this, List.mem_cons, Ne.symm hk]

theorem dedupFast_eq (l : List String) : dedupFast l = dedup l := (dedupFast_aux l).2

end Figram

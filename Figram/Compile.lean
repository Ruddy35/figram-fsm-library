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
`compile`: те же данные, но с индексами (раздел «Индексы») — предки каждого
сообщения считаются один раз, для сообщения перебираются только стрелки
его предков, места «вернуться» собираются за один проход. Равенство
`compile_eq_spec : c.compile = c.compileSpec` доказано в `Figram.Correct`.
-/

namespace Figram

/-! ## Индексы

Хеш-таблицы, которые компилятор и проверка строят один раз на документ.
Только `Init` (без `Std`): библиотека для встраивания (`ffi/build.sh`)
собирается из тех же модулей, что и раньше. Каждая таблица — быстрая
реализация спискового определения, равенство доказано (`keySet_contains`,
`firstIndex_get`, `concatIndex_get`, `dedupFast_eq`), поэтому остальные
доказательства говорят о простых списках. -/

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

/-- Корзина ключа в таблице из `n` корзин. Остаток считается в `UInt64`
(без длинной арифметики); внешний `% n` гарантирует `slot n k < n`. -/
def slotOf (n : Nat) (h : UInt64) : Nat := (h % n.toUInt64).toNat % n

def slot (n : Nat) (k : String) : Nat := slotOf n (hash k)

/-- Хеш-таблица с фиксированным числом корзин; пары корзины — в порядке вставки
(последняя вставленная — первая). -/
structure Tab (β : Type) where
  buckets : Array (List (String × β))

namespace Tab

/-- Пустая таблица на `n + 1` корзину. -/
def empty {β} (n : Nat) : Tab β := ⟨Array.replicate (n + 1) []⟩

/-- Вставка; сопоставление с образцом отдаёт массив корзин единственным
владельцем, и `modify` меняет его на месте. -/
def insert {β} : Tab β → String → β → Tab β
  | ⟨b⟩, k, v => ⟨b.modify (slot b.size k) ((k, v) :: ·)⟩

def bucket {β} (t : Tab β) (k : String) : List (String × β) :=
  t.buckets.getD (slot t.buckets.size k) []

/-- Первое значение ключа. -/
def find? {β} (t : Tab β) (k : String) : Option β :=
  ((t.bucket k).find? (fun p => decide (p.1 = k))).map (·.2)

def contains {β} (t : Tab β) (k : String) : Bool := (t.bucket k).any (fun p => decide (p.1 = k))

/-- Значения ключа `k` в списке пар подряд; без лишних выделений памяти:
нет пары — `[]`, одна пара — её список без копии. -/
def concatIn {β} (k : String) : List (String × List β) → List β
  | [] => []
  | p :: ps =>
    if p.1 = k then
      match concatIn k ps with
      | [] => p.2
      | rest => p.2 ++ rest
    else concatIn k ps

/-- Все значения ключа подряд. -/
def concat {β} (t : Tab (List β)) (k : String) : List β := concatIn k (t.bucket k)

/-- `concat` с заранее посчитанным хешем ключа (`concatH t k (hash k) = concat t k`). -/
def concatH {β} (t : Tab (List β)) (k : String) (h : UInt64) : List β :=
  concatIn k (t.buckets.getD (slotOf t.buckets.size h) [])

theorem concatH_hash {β} (t : Tab (List β)) (k : String) : t.concatH k (hash k) = t.concat k := rfl

/-- Таблица из списка пар: корзины хранят пары в порядке списка; корзин вдвое
больше пар, чтобы поиск отсутствующего ключа чаще попадал в пустую корзину. -/
def ofList {β} (l : List (String × β)) : Tab β :=
  l.foldr (fun p t => t.insert p.1 p.2) (empty (2 * l.length))

theorem size_insert {β} (t : Tab β) (k : String) (v : β) :
    (t.insert k v).buckets.size = t.buckets.size := by
  simp [insert]

theorem bucket_insert {β} (t : Tab β) (k : String) (v : β) (k' : String)
    (h : 0 < t.buckets.size) :
    (t.insert k v).bucket k' =
      if slot t.buckets.size k = slot t.buckets.size k' then (k, v) :: t.bucket k' else t.bucket k' := by
  simp only [bucket, size_insert, Array.getD_eq_getD_getElem?]
  simp only [insert, Array.getElem?_modify]
  split
  · rename_i he
    have hlt : slot t.buckets.size k' < t.buckets.size := Nat.mod_lt _ h
    rw [← he] at hlt ⊢
    simp [Array.getElem?_eq_getElem hlt]
  · rfl

theorem ofList_aux {β} (l : List (String × β)) (n : Nat) (k : String) :
    (l.foldr (fun p t => t.insert p.1 p.2) (empty n : Tab β)).buckets.size = n + 1 ∧
    (l.foldr (fun p t => t.insert p.1 p.2) (empty n : Tab β)).bucket k =
      l.filter (fun p => decide (slot (n + 1) p.1 = slot (n + 1) k)) := by
  induction l with
  | nil =>
    refine ⟨by simp [empty], ?_⟩
    simp only [List.foldr_nil, List.filter_nil, bucket, empty, Array.size_replicate,
      Array.getD_eq_getD_getElem?, Array.getElem?_replicate]
    split <;> rfl
  | cons p ps ih =>
    obtain ⟨hs, hb⟩ := ih
    simp only [List.foldr_cons]
    refine ⟨by rw [size_insert, hs], ?_⟩
    rw [bucket_insert _ _ _ _ (by rw [hs]; omega), hs, hb, List.filter_cons]
    by_cases h : slot (n + 1) p.1 = slot (n + 1) k <;> simp [h]

theorem bucket_ofList {β} (l : List (String × β)) (k : String) :
    (ofList l).bucket k =
      l.filter (fun p => decide (slot (2 * l.length + 1) p.1 = slot (2 * l.length + 1) k)) :=
  (ofList_aux l (2 * l.length) k).2

end Tab

/-- Поиск в отфильтрованном по корзине списке — тот же, что во всём списке. -/
theorem find?_slot {β} (l : List (String × β)) (f : String → Nat) (k : String) :
    (l.filter (fun p => decide (f p.1 = f k))).find? (fun p => decide (p.1 = k)) =
      l.find? (fun p => decide (p.1 = k)) := by
  induction l with
  | nil => rfl
  | cons p ps ih =>
    rw [List.filter_cons]
    by_cases hf : f p.1 = f k
    · rw [ite_eq_left (by simpa using hf), List.find?_cons, List.find?_cons, ih]
    · have h : p.1 ≠ k := fun he => hf (he ▸ rfl)
      rw [ite_eq_right (by simpa using hf), ih, List.find?_cons]
      simp [h]

theorem filter_slot {β} (l : List (String × β)) (f : String → Nat) (k : String) :
    (l.filter (fun p => decide (f p.1 = f k))).filter (fun p => decide (p.1 = k)) =
      l.filter (fun p => decide (p.1 = k)) := by
  induction l with
  | nil => rfl
  | cons p ps ih =>
    rw [List.filter_cons]
    by_cases hf : f p.1 = f k
    · rw [ite_eq_left (by simpa using hf), List.filter_cons, List.filter_cons, ih]
    · have h : p.1 ≠ k := fun he => hf (he ▸ rfl)
      rw [ite_eq_right (by simpa using hf), ih, List.filter_cons]
      simp [h]

/-- Множество строк списка. -/
def keySet (l : List String) : Tab Unit := Tab.ofList (l.map fun k => (k, ()))

theorem keySet_contains (l : List String) (k : String) :
    (keySet l).contains k = decide (k ∈ l) := by
  simp only [keySet, Tab.contains, Tab.bucket_ofList]
  apply Bool.eq_iff_iff.mpr
  simp only [List.any_eq_true, List.mem_filter, List.mem_map, decide_eq_true_eq]
  constructor
  · rintro ⟨p, ⟨⟨x, hx, rfl⟩, _⟩, rfl⟩; exact hx
  · intro hk; exact ⟨(k, ()), ⟨⟨k, hk, rfl⟩, by simp⟩, rfl⟩

/-- Первая запись с ключом — то же, что поиск по списку пар. -/
def firstIndex {β} (l : List (String × β)) : Tab β := Tab.ofList l

theorem firstIndex_get {β} (l : List (String × β)) (k : String) :
    (firstIndex l).find? k = (l.find? (fun p => decide (p.1 = k))).map (·.2) := by
  simp only [firstIndex, Tab.find?, Tab.bucket_ofList, find?_slot]

/-- Сцепление списков по ключу с сохранением порядка: для ключа `k` —
списки всех пар с ключом `k`, подряд в порядке пар. -/
def concatIndex {β} (l : List (String × List β)) : Tab (List β) := Tab.ofList l

theorem concatIn_eq {β} (k : String) (ps : List (String × List β)) :
    Tab.concatIn k ps = (ps.filter (fun p => decide (p.1 = k))).flatMap (·.2) := by
  induction ps with
  | nil => rfl
  | cons p ps ih =>
    unfold Tab.concatIn
    rw [List.filter_cons]
    by_cases h : p.1 = k
    · rw [ite_eq_left h, ite_eq_left (by simpa using h), List.flatMap_cons, ← ih]
      split
      · rename_i he; rw [he]; simp
      · rfl
    · rw [ite_eq_right h, ite_eq_right (by simpa using h), ih]

theorem concatIndex_get {β} (l : List (String × List β)) (k : String) :
    (concatIndex l).concat k = (l.filter (fun p => decide (p.1 = k))).flatMap (·.2) := by
  simp only [concatIndex, Tab.concat, concatIn_eq, Tab.bucket_ofList, filter_slot]

/-- Шаг `dedupFast`. -/
def dedupStep (x : String) : Tab Unit × List String → Tab Unit × List String
  | (seen, acc) => if seen.contains x then (seen, acc) else (seen.insert x (), x :: acc)

/-- Удаление повторов за линейное время; равно `dedup` (`dedupFast_eq`). -/
def dedupFast (l : List String) : List String := (l.foldr dedupStep (Tab.empty l.length, [])).2

theorem contains_insert (t : Tab Unit) (x k : String) (h : 0 < t.buckets.size) :
    (t.insert x ()).contains k = (decide (x = k) || t.contains k) := by
  simp only [Tab.contains, Tab.bucket_insert _ _ _ _ h]
  split
  · simp
  · rename_i hs
    have : x ≠ k := fun he => hs (he ▸ rfl)
    simp [this]

theorem dedupFast_aux (l : List String) (n : Nat) :
    (l.foldr dedupStep (Tab.empty n, [])).1.buckets.size = n + 1 ∧
    (∀ k, (l.foldr dedupStep (Tab.empty n, [])).1.contains k = decide (k ∈ l)) ∧
    (l.foldr dedupStep (Tab.empty n, [])).2 = dedup l := by
  induction l with
  | nil =>
    refine ⟨by simp [Tab.empty], fun k => ?_, by simp [dedup]⟩
    simp only [List.foldr_nil, Tab.contains, Tab.bucket, Tab.empty, Array.size_replicate,
      Array.getD_eq_getD_getElem?, Array.getElem?_replicate]
    split <;> simp
  | cons x xs ih =>
    obtain ⟨hs, ih1, ih2⟩ := ih
    simp only [List.foldr_cons]
    generalize xs.foldr dedupStep (Tab.empty n, []) = q at hs ih1 ih2 ⊢
    unfold dedupStep
    by_cases hx : x ∈ xs
    · have hc : q.1.contains x = true := by rw [ih1]; simpa using hx
      have hd : x ∈ dedup xs := (mem_dedup x xs).mpr hx
      simp only [hc, ite_true, dedup, hd]
      refine ⟨hs, fun k => ?_, ih2⟩
      rw [ih1]
      by_cases hk : k = x
      · subst hk; simp [hx]
      · simp [List.mem_cons, hk]
    · have hc : q.1.contains x = false := by rw [ih1]; simpa using hx
      have hd : x ∉ dedup xs := fun h => hx ((mem_dedup x xs).mp h)
      simp only [hc, Bool.false_eq_true, ite_false, dedup, hd, ih2, and_true]
      refine ⟨by rw [Tab.size_insert, hs], fun k => ?_⟩
      rw [contains_insert _ _ _ (by rw [hs]; omega), ih1]
      by_cases hk : x = k
      · subst hk; simp
      · simp [hk, List.mem_cons, Ne.symm hk]

theorem dedupFast_eq (l : List String) : dedupFast l = dedup l := (dedupFast_aux l l.length).2.2

namespace Machine

/-- `targetOK` по готовым множествам сообщений и разделов. -/
def targetIn (st secs : Tab Unit) : Target → Bool
  | .node n => st.contains n
  | .resume sec => secs.contains sec

/-- `wf` за линейное время: принадлежность — через хеш-таблицы
(`wfFast_eq : m.wfFast = m.wf` в `Figram.Correct`). -/
def wfFast (m : Machine) : Bool :=
  let st := keySet m.states
  let secs := keySet (m.resumes.map (·.sec))
  st.contains m.initial &&
  m.table.all (fun g => g.2.all (fun r => r.branches.all (fun b => targetIn st secs b.target))) &&
  m.resumes.all (fun r => st.contains r.default && r.members.all (fun v => st.contains v))

end Machine

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
  node : Tab Node
  hist : Tab Unit
  timers : Tab (List Timer)

def index (c : Chart) : Index where
  fuel := c.nodes.length
  node := firstIndex (c.nodes.map fun x => (x.id, x))
  hist := keySet c.histSecs
  timers := concatIndex (c.timers.map fun t => (t.node, [t]))

namespace Index

def parent (ix : Index) (n : NodeId) : Option NodeId := (ix.node.find? n).bind (·.parent)

def ancAux (ix : Index) : Nat → NodeId → List NodeId
  | 0, n => [n]
  | k + 1, n => n :: match ix.parent n with
    | none => []
    | some p => ix.ancAux k p

def anc (ix : Index) (n : NodeId) : List NodeId := ix.ancAux ix.fuel n

def enterAux (ix : Index) : Nat → NodeId → NodeId
  | 0, n => n
  | k + 1, n => match ix.node.find? n with
    | some ⟨_, _, .sec i⟩ => ix.enterAux k i
    | _ => n

def enter (ix : Index) (n : NodeId) : NodeId := ix.enterAux ix.fuel n

def toF (ix : Index) (b : Branch) : FBranch :=
  ⟨b.guard, (match b.target with
    | .node n => .node (ix.enter n)
    | .history s => .resume s), b.actions⟩

def timersOf (ix : Index) (n : NodeId) : List Timer := ix.timers.concat n

def entryActs (ix : Index) (T : NodeId) : List Action :=
  (ix.anc T).filterMap (fun s =>
      if (ix.hist.contains s && s != T) = true then some (.remember s T) else none)
  ++ (ix.timersOf T).map (fun t => .startTimer t.id t.delayMs 0)
  ++ [.send T]

def exitActs (ix : Index) (L : NodeId) : List Action :=
  (ix.timersOf L).map (fun t => .cancelTimer t.id)

/-- Цель стрелки ведёт в сообщение из `leaves` («вернуться» проверяется отдельно). -/
def destIn (ix : Index) (leaves : Tab Unit) : Dest → Bool
  | .node n => leaves.contains (ix.enter n)
  | .history _ => true

end Index

/-- Стрелки каждого узла с уже переведёнными вариантами:
узел → список (событие, варианты) в порядке стрелок. -/
def transIndex (c : Chart) (ix : Index) : Tab (List (EventId × List FBranch)) :=
  concatIndex (c.transitions.map fun t => (t.src, [(t.event, t.branches.map ix.toF)]))

/-- Стрелки сообщения `L`: варианты всех его предков, сложенные по событиям,
в порядке событий документа `evs`. -/
def leafRows (evs : List (EventId × UInt64)) (tr : Tab (List (EventId × List FBranch)))
    (ix : Index) (L : NodeId) : List Row :=
  let tab := concatIndex ((ix.anc L).flatMap fun a => tr.concat a)
  evs.filterMap fun (e, h) =>
    let bs := tab.concatH e h
    if bs.isEmpty then none else some ⟨L, e, bs⟩

/-- Сообщения внутри разделов с «вернуться туда, где был», за один проход. -/
def memberIndex (c : Chart) (ix : Index) : Tab (List NodeId) :=
  concatIndex (c.leafIds.flatMap fun l =>
    (dedupFast ((ix.anc l).filter fun a => ix.hist.contains a && a != l)).map fun a => (a, [l]))

/-- **Компилятор** (быстрая реализация `compileSpec`). -/
def compile (c : Chart) : Machine :=
  let ix := c.index
  let tr := c.transIndex ix
  -- события документа с готовыми хешами: на каждое сообщение — без пересчёта
  let evs := (dedupFast (c.transitions.map (·.event))).map fun e => (e, hash e)
  let mem := c.memberIndex ix
  let leaves := c.leafIds
  { states := leaves
    initial := ix.enter c.root
    initActions := ix.entryActs (ix.enter c.root)
    table := leaves.map fun L => (L, leafRows evs tr ix L)
    resumes := (dedupFast c.histSecs).map fun s => ⟨s, ix.enter s, mem.concat s⟩
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

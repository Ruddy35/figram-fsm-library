import Figram.Engine

/-!
# Эталонные сценарии

Ожидаемые результаты считаются по **эталонной семантике схемы** (`Chart.step`),
а не по скомпилированному автомату. Имена нужных условий тоже берутся из схемы
(`guardNames`), чтобы вход сценария не зависел от проверяемого компилятора.

Формат строки сценария (JSONL, по строке на сценарий):

```
{"name"?: "...", "from"?: <снимок>, "start"?: {"snapshot": ..., "actions": [...]},
 "steps": [{"event": "...", "token"?: n, "guards": {...},
            "snapshot": ..., "actions": [...], "recovered"?: true}]}
```

Если есть `from`, сценарий начинается с этого снимка, иначе — с `start`.
-/

namespace Figram.Trace
open Figram.Format

def lcg (s : Nat) : Nat := (s * 6364136223846793005 + 1442695040888963407) % 2 ^ 64

/-- Имена условий, которые нужны для шага, по эталонной семантике. -/
def guardNames (c : Chart) (s : Snapshot) (ev : Event) : List String :=
  if s.state ∈ c.leafIds && s.accepts ev then
    dedup ((c.cands s.state ev.key).filterMap (·.guard))
  else []

/-- Индекс сработавшего варианта среди кандидатов (для покрытия). -/
def firedIndex (c : Chart) (s : Snapshot) (ev : Event) (env : HostEnv) : Option Nat :=
  if s.state ∈ c.leafIds && s.accepts ev then
    (c.cands s.state ev.key).findIdx? (fun b => guardOK env b.guard)
  else none

def envOf (gs : List (String × Bool)) : HostEnv := fun n => ((gs.find? (·.1 == n)).map (·.2)).getD false

/-- Один шаг: новый снимок и строка JSON шага. -/
def stepJ (c : Chart) (s : Snapshot) (ev : Event) (gs : List (String × Bool)) : Snapshot × J :=
  let (s', acts) := c.step s ev (envOf gs)
  let recovered := decide (s.state ∉ c.leafIds)
  let tok : Obj := match ev with
    | .timer _ t => [("token", .num t 0)]
    | .named _ => []
  (s', .obj ([("event", .str ev.key)] ++ tok ++
    [("guards", .obj (gs.map fun p => (p.1, .bool p.2))),
     ("snapshot", Engine.encSnapshot s'), ("actions", Engine.encActions acts)]
    ++ field "recovered" recovered (.bool true)))

def startJ (c : Chart) : J :=
  let (s, a) := c.start
  .obj [("snapshot", Engine.encSnapshot s), ("actions", Engine.encActions a)]

/-! ## Случайный сценарий -/

/-- Насколько «близко» к сообщению определено событие: 0 — на самом сообщении. -/
def depthOf (c : Chart) (L : NodeId) (e : EventId) : Nat :=
  ((c.anc L).findIdx? (fun a => !(c.branchesAt a e).isEmpty)).getD 99

structure Gen where
  c : Chart
  s : Snapshot
  r : Nat
  steps : Array J := #[]
  covered : List String := []

def Gen.rnd (g : Gen) (n : Nat) : Nat × Gen :=
  let r := lcg g.r
  ((r >>> 33) % max n 1, { g with r := r })

/-- Выбор события: события самого сообщения в 8 раз вероятнее глобальных,
таймеры срабатывают с текущим или устаревшим жетоном, изредка — незнакомое событие. -/
def Gen.pickEvent (g : Gen) : Event × Gen := Id.run do
  let (roll, g) := g.rnd 100
  if roll < 15 && !g.s.timers.isEmpty then
    let (i, g) := g.rnd g.s.timers.length
    let (id, tok) := g.s.timers.getD i ("", 0)
    return (.timer id tok, g)
  if roll < 20 && !g.s.timers.isEmpty then
    let (i, g) := g.rnd g.s.timers.length
    let (id, tok) := g.s.timers.getD i ("", 0)
    return (.timer id (if tok > 0 then tok - 1 else tok + 1), g)
  if roll < 23 then return (.named "unknown:event", g)
  let L := g.s.state
  let live := g.c.events.filter (fun e => !e.startsWith "timer:" && !(g.c.cands L e).isEmpty)
  if live.isEmpty then return (.named "unknown:event", g)
  let weighted := live.flatMap fun e =>
    List.replicate (2 ^ (3 - min (depthOf g.c L e) 3)) e
  let (i, g) := g.rnd weighted.length
  return (.named (weighted.getD i "unknown:event"), g)

def Gen.step (g : Gen) : Gen := Id.run do
  let (ev, g) := g.pickEvent
  let mut g := g
  let mut gs : List (String × Bool) := []
  for n in guardNames g.c g.s ev do
    let (b, g') := g.rnd 2
    g := g'
    gs := gs ++ [(n, b == 0)]
  let cov := match firedIndex g.c g.s ev (envOf gs) with
    | some i => [s!"{g.s.state}|{ev.key}|{i}"]
    | none => []
  let (s2, j) := stepJ g.c g.s ev gs
  let cov2 := cov.foldl (fun acc x => if acc.contains x then acc else x :: acc) g.covered
  return { g with s := s2, steps := g.steps.push j, covered := cov2 }

/-- Случайный сценарий и покрытые им варианты стрелок. -/
def random (c : Chart) (len seed : Nat) : J × List String := Id.run do
  let mut g : Gen := { c := c, s := c.start.1, r := seed }
  for _ in [0:len] do
    g := g.step
  return (.obj [("start", startJ c), ("steps", .arr g.steps.toList)], g.covered)

/-- Всего вариантов стрелок у всех сообщений (знаменатель покрытия). -/
def totalBranches (c : Chart) : Nat :=
  c.leafIds.foldl (fun acc L => acc + c.events.foldl (fun a e => a + (c.cands L e).length) 0) 0

/-! ## Сценарий по сценарию -/

inductive In where
  | ev (name : String) (guards : List (String × Bool) := [])
  /-- Срабатывание таймера с текущим жетоном. -/
  | timerNow (id : String)
  /-- Срабатывание таймера с устаревшим жетоном. -/
  | timerStale (id : String) (token : Nat)

def scripted (c : Chart) (name : String) (from? : Option Snapshot) (ins : List In) :
    J × Snapshot × List (List Action) := Id.run do
  let mut s := from?.getD c.start.1
  let mut steps : Array J := #[]
  let mut acts : List (List Action) := []
  for i in ins do
    let (ev, given) := match i with
      | .ev n gs => (Event.named n, gs)
      | .timerNow id => (Event.timer id ((lookup s.timers id).getD 0), [])
      | .timerStale id t => (Event.timer id t, [])
    -- в сценарий попадают ровно нужные шагу условия (по эталонной семантике)
    let gs := (guardNames c s ev).map fun n => (n, ((given.find? (·.1 == n)).map (·.2)).getD false)
    let a := (c.step s ev (envOf gs)).2
    let (s', j) := stepJ c s ev gs
    steps := steps.push j
    acts := acts ++ [a]
    s := s'
  let head : Obj := match from? with
    | some f => [("from", Engine.encSnapshot f)]
    | none => [("start", startJ c)]
  return (.obj ([("name", .str name)] ++ head ++ [("steps", .arr steps.toList)]), s, acts)

end Figram.Trace

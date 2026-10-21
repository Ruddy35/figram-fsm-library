import Figram.Format

/-!
# Random documents

A generator of valid documents of various shapes: nested sections, arrows
on messages and sections (bubbling), conditions on both sides, "resume where
the user left off", reminders with handlers. It lets the scenarios test the
engine on more than one bot.
-/

namespace Figram.RandomDoc
open Figram.Format

def lcg (s : Nat) : Nat := (s * 6364136223846793005 + 1442695040888963407) % 2 ^ 64

abbrev M := StateM Nat

def rnd (n : Nat) : M Nat := modifyGet fun s => let s' := lcg s; ((s' >>> 33) % max n 1, s')
def chance (p : Nat) : M Bool := do return (← rnd 100) < p
def pick (l : List String) : M String := do return l.getD (← rnd l.length) ""

def conds : List String := ["c1", "c2", "c3"]
def calls : List String := ["k1", "k2", "k3"]
def events : List String := ["btn:a", "btn:b", "btn:c", "msg:text"]

def variants (nodes secs : List String) : M (List Variant) := do
  let cnt := 1 + (← rnd 3)
  let mut vs : List Variant := []
  for j in List.range cnt do
    let guarded ← if j + 1 < cnt then pure true else chance 25
    let cond ← if guarded then some <$> pick conds else pure none
    let resume ← if secs.isEmpty then pure false else chance 20
    let to ← if resume then pick secs else pick nodes
    let mut cs : List String := []
    for _ in List.range (← rnd 3) do
      cs := cs ++ [← pick calls]
    vs := vs ++ [⟨cond, to, resume, cs, []⟩]
  return vs

def handlers (nodes secs : List String) (pool : List String) (p : Nat) :
    M (List (EventId × List Variant)) := do
  let mut hs : List (EventId × List Variant) := []
  for e in pool do
    if ← chance p then
      hs := hs ++ [(e, ← variants nodes secs)]
  return hs

structure Plan where
  parent : List (String × String)
  isSec : List String
  on : List (String × List (EventId × List Variant))
  timers : List (String × List (String × Nat))

instance : Inhabited NodeDoc := ⟨.message [] [] []⟩

partial def build (p : Plan) (id : String) : NodeDoc :=
  let kids := (p.parent.filter (·.2 == id)).map (·.1)
  let on := (lookup p.on id).getD []
  if id == "" || p.isSec.contains id then
    .sec (kids.headD "") (kids.map fun k => (k, build p k)) on []
  else .message on ((lookup p.timers id).getD []) []

def gen (n : Nat) : M Doc := do
  let n := max n 2
  let k := n / 3
  let secs := (List.range k).map (s!"s{·}")
  let msgs := (List.range n).map (s!"m{·}")
  let nodes := msgs ++ secs
  let mut parent : List (String × String) := []
  for i in List.range k do
    let pr ← if i == 0 then pure "" else do
      if ← chance 50 then pure "" else pure s!"s{← rnd i}"
    parent := parent ++ [(s!"s{i}", pr)]
  for i in List.range n do
    let pr ← if i < k then pure s!"s{i}" else do
      let r ← rnd (k + 1)
      pure (if r == k then "" else s!"s{r}")
    parent := (s!"m{i}", pr) :: parent
  let mut on : List (String × List (EventId × List Variant)) := []
  let mut timers : List (String × List (String × Nat)) := []
  for (m, i) in msgs.zipIdx do
    let mut hs ← handlers nodes secs events 35
    if ← chance 30 then
      let t := s!"t{i}"
      timers := timers ++ [(m, [(t, 1 + (← rnd 100000))])]
      hs := hs ++ [(timerEvent t, ← variants nodes secs)]
    on := on ++ [(m, hs)]
  for sc in secs do
    on := on ++ [(sc, ← handlers nodes secs events 15)]
  on := on ++ [("", ← handlers nodes secs ["cmd:start", "cmd:help"] 60)]
  let plan : Plan := ⟨parent, secs, on, timers⟩
  return {
    requires := [],
    conditions := conds.map fun c => (c, s!"Random condition {c}"),
    calls := calls.map fun c => (c, s!"Random action {c}"),
    variables := [],
    bot := build plan "",
    ext := [] }

def doc (seed n : Nat) : Doc := ((gen n).run' (seed * 2654435761 + 7))

end Figram.RandomDoc

import Figram.Compile

/-!
# Checking a chart before export

The errors an editor shows to the designer. The decisive check is the final one,
`wfDirect`: it equals `c.compile.wf` (`wfDirect_eq`), the hypothesis of the theorems of
`Figram.Run`; hence `lint_nil_wf` (`Figram.LintProofs`). The other checks catch errors earlier
and explain them in plain words. The check does not build the machine: a document
is compiled once, when it is loaded.

Warnings (`lintWarnings`) are about things that are valid but never fire
or behave unexpectedly. They do not affect the result of the check.
-/

namespace Figram
namespace Chart

def exists_ (c : Chart) (n : NodeId) : Bool := (c.find n).isSome

def isSec (c : Chart) (n : NodeId) : Bool :=
  match c.find n with
  | some ⟨_, _, .sec _⟩ => true
  | _ => false

/-- The name of a node in messages: the document root (the empty name) is called `bot`. -/
def shown (n : NodeId) : String := if n.isEmpty then "bot" else n

def lintMsg : String := "chart fails the final check: some arrow does not lead to a message"

/-- The errors of a chart, except the final check `wfDirect`. Nodes are looked up in the
index (`Chart.index`, the same as `find`), so the check is linear. -/
def lintCore (c : Chart) : List String := Id.run do
  let ix := c.index
  let found (n : NodeId) : Bool := (ix.node.find? n).isSome
  let sec (n : NodeId) : Bool := match ix.node.find? n with
    | some ⟨_, _, .sec _⟩ => true
    | _ => false
  -- declaration positions by name: a duplicate is reported once, at the first declaration
  let pos := concatIndex (c.nodes.zipIdx.map fun (n, i) => (n.id, [i]))
  let mut errs : Array String := #[]
  for (n, i) in c.nodes.zipIdx do
    let ps := pos.concat n.id
    if ps.length > 1 && ps.head? == some i then
      errs := errs.push s!"node `{shown n.id}` is declared more than once"
    match n.parent with
    | some p =>
      if !found p then errs := errs.push s!"`{shown n.id}` has a nonexistent parent section `{shown p}`"
      else if !sec p then errs := errs.push s!"parent `{shown p}` of node `{shown n.id}` is not a section"
    | none => pure ()
    -- a cycle: climbing the parents hits the limit
    if (ix.anc n.id).length > ix.fuel then
      errs := errs.push s!"cycle in the nesting of sections through `{shown n.id}`"
    match n.kind with
    | .sec i =>
      if (ix.node.find? i).bind (·.parent) != some n.id then
        errs := errs.push
          s!"initial node `{shown i}` of section `{shown n.id}` must be a child of it"
    | .message => pure ()
  if !found c.root then errs := errs.push s!"start node `{shown c.root}` does not exist"
  for t in c.transitions do
    if !found t.src then
      errs := errs.push s!"arrow on `{t.event}` starts at nonexistent `{shown t.src}`"
    for b in t.branches do
      match b.target with
      | .node n =>
        if !found n then errs := errs.push s!"arrow `{shown t.src}` → `{shown n}`: target does not exist"
      | .history s =>
        if !sec s then
          errs := errs.push
            s!"arrow `{shown t.src}` → `{shown s}` with `resume`: not a section"
  let leaves := keySet c.leafIds
  for tm in c.timers do
    if !leaves.contains tm.node then
      errs := errs.push s!"timer `{tm.id}` is not on a message"
  return errs.toList

/-- The errors of a chart. The final check is `wfDirect` (= `c.compile.wf`). -/
def lint (c : Chart) : List String :=
  let errs := c.lintCore
  if errs.isEmpty && !c.wfDirect then errs ++ [lintMsg] else errs

/-! ## Warnings -/

/-- The timer `X` of the event `timer:X`. -/
def timerOfEvent (e : EventId) : Option String :=
  if e.startsWith "timer:" then some (e.drop 6).toString else none

/-- Warnings about a chart that passed the check: dead variants, unreachable
messages, arrows that never fire, and timer loops that keep sibling timers
from firing. -/
def lintWarnings (c : Chart) : List String := Id.run do
  let ix := c.index
  let m := c.compile
  let mut ws : Array String := #[]
  -- 1. Variants of an arrow after a variant without a condition, and a repeated condition.
  for t in c.transitions do
    let mut uncond : Option Nat := none
    let mut seen : List String := []
    let mut k := 0
    for b in t.branches do
      k := k + 1
      match uncond with
      | some u =>
        ws := ws.push (s!"arrow `{shown t.src}` on `{t.event}`: variant {k} never fires, " ++
          s!"variant {u} before it has no condition")
      | none =>
        match b.guard with
        | none => uncond := some k
        | some g =>
          if seen.contains g then
            ws := ws.push (s!"arrow `{shown t.src}` on `{t.event}`: condition `{g}` is repeated, " ++
              s!"variant {k} never fires")
          else seen := g :: seen
  -- 2. Arrows `timer:X` where timer X is never started: a message without X
  --    or a section with no message with X inside.
  let owners := concatIndex (c.timers.map fun tm => (tm.id, [tm.node]))
  for t in c.transitions do
    match timerOfEvent t.event with
    | none => pure ()
    | some x =>
      let os := owners.concat x
      unless os.any (fun o => (ix.anc o).contains t.src) do
        ws := ws.push (s!"arrow `{t.event}` on `{shown t.src}` never fires: only other messages start " ++
          s!"timer `{x}`, and a firing is accepted only by the message that started it")
  -- 3. "Resume" drawn only from inside the section.
  for s in dedupFast c.histSecs do
    let srcs := c.transitions.filter fun t => t.branches.any fun b => b.target == .history s
    if srcs.all (fun t => (ix.anc t.src).contains s) then
      ws := ws.push (s!"arrows with `resume` into `{shown s}` are drawn only from inside the section: " ++
        "they always leave the user on the current message")
  -- 4. Reachability of messages from the start of the bot through arrows that can fire.
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
          -- variants after an unconditional one or with an already seen condition never fire
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
      ws := ws.push s!"message `{shown L}` is unreachable: no arrow leads to it from the start of the bot"
  -- 5. A timer loop to its own message also restarts the sibling timers.
  for L in m.states do
    let mine := ix.timersOf L
    for tm in mine do
      match m.row L (timerEvent tm.id) with
      | none => pure ()
      | some r =>
        if (untilUnguarded (·.guard) r.branches).any (fun b => b.target == .node L) then
          let starved := mine.filter fun o => o.id != tm.id && o.delayMs ≥ tm.delayMs
          unless starved.isEmpty do
            ws := ws.push (s!"loop `{timerEvent tm.id}` on `{shown L}` restarts the message's timers " ++
              s!"({", ".intercalate (starved.map fun o => s!"`{o.id}`")}): while `{tm.id}` keeps firing, " ++
              "they never fire")
  return ws.toList

end Chart
end Figram

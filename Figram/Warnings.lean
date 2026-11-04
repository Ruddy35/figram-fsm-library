import Figram.Format

/-!
# Warnings about a document

What passes `Doc.check` but is most likely a designer's mistake: variants and
arrows that never fire, unreachable messages, timer loops that keep sibling
timers from firing (`Chart.lintWarnings`), outcomes that a waiting message does not handle
(`Doc.outcomeWarnings`). Warnings affect neither `check` nor
the behaviour of the engine: a document that was valid stays valid.
`figram check` prints them to stderr with the prefix `warning:` and
exits with status 0.
-/

namespace Figram.Format

/-- A variant with an action with outcomes leads to a message (or, without a target, stays on
the messages under its arrow where it fires) where an outcome of that action, `failed` included, has no arrow
on the message or its sections: the outcome would be ignored, and a user waiting there would
wait for good. Variants with "resume" or "back" are not checked (their target depends on the
snapshot).

Second: the waiting message is a "please wait" message (all arrows drawn on it are outcome
arrows) and handles an outcome, but an arrow of a section leads from it to a message where that
outcome does not move the user (no arrow, or only variants without a target), and the user can
come back to the waiting message by "back" (the document has such a variant) or "resume" (into
a section containing it): an outcome that arrives on the other message leaves the user there,
and the user returns to "please wait" with nothing to wait for. One warning per waiting message
and action. -/
def Doc.outcomeWarnings (d : Doc) : List String := Id.run do
  let c := d.toChart
  let ix := c.index
  let m := c.compile
  let backUsed := c.transitions.any fun t => t.branches.any fun b =>
    match b.target with | .back _ => true | _ => false
  let resumedInto (M : NodeId) : Bool := c.transitions.any fun t => t.branches.any fun b =>
    match b.target with
    | .history s => (ix.anc M).contains s
    | .back (.history s) => (ix.anc M).contains s
    | _ => false
  -- the messages an arrow of the machine can lead to (a resumed section: any of its messages)
  let landings : Target → List NodeId
    | .node x => [x]
    | .resume s => match m.resumes.find? (·.sec == s) with
      | some r => r.default :: r.members
      | none => []
    | .back (.node x) => [x]
    | _ => []
  let pleaseWait (M : NodeId) : Bool :=
    let own := c.transitions.filter (·.src == M)
    !own.isEmpty && own.all (·.event.startsWith "call:")
  let mut ws : Array String := #[]
  let mut seen : Array (NodeId × String) := #[]
  for t in c.transitions do
    for b in t.branches do
      for a in b.actions do
        match a with
        | .call n _ w _ =>
          if w != .free then
            let os := ((lookup d.calls n).map (·.outcomes)).getD [] ++ ["failed"]
            let waiting : List NodeId := match b.target with
              | .node x => [ix.enter x]
              -- the messages under the arrow where it fires: for a timer, only those that own it
              | .stay => c.leafIds.filter fun l => (ix.anc l).contains t.src &&
                  match Chart.timerOfEvent t.event with
                  | some x => (m.exitOf l).contains (.cancelTimer x)
                  | none => true
              | _ => []
            for M in waiting do
              for o in os do
                if (m.row M (outcomeEvent n o)).isNone then
                  ws := ws.push (s!"outcome `{o}` of action `{n}` has no arrow on `{Chart.shown M}`: " ++
                    s!"the variant of `{Chart.shown t.src}` on `{t.event}` waits for it there " ++
                    "(an outcome without an arrow is ignored)")
              if pleaseWait M && (backUsed || resumedInto M) && !seen.contains (M, n) then
                let handled := os.filter fun o => (m.row M (outcomeEvent n o)).isSome
                let exits := ((lookup m.table M).getD []).filter fun r => !os.any (r.event == outcomeEvent n ·)
                -- the outcome does not move the user from `L`: no arrow there, or only variants
                -- without a target
                let stays (L : NodeId) (o : String) : Bool := match m.row L (outcomeEvent n o) with
                  | none => true
                  | some r => r.branches.all (·.target == .stay)
                let lost := exits.flatMap fun r => r.branches.flatMap fun fb =>
                  (landings fb.target).filterMap fun L =>
                    if L == M then none else
                    (handled.find? fun o => stays L o).map fun o => (r.event, L, o)
                if let some (e, L, o) := lost.head? then
                  seen := seen.push (M, n)
                  ws := ws.push (s!"outcome `{o}` of action `{n}` does not move the user from `{Chart.shown L}` " ++
                    s!"(no arrow, or one without a target), where `{e}` leads from `{Chart.shown M}`, which " ++
                    s!"waits for it: `back` or `resume` can then return the user to `{Chart.shown M}` with " ++
                    "nothing to wait for (draw on a section or the root arrows of the outcome that lead on)")
        | _ => pure ()
  return ws.toList

/-- Warnings about a document (only for a document without `check` errors). -/
def Doc.warnings (d : Doc) : List String := d.toChart.lintWarnings ++ d.outcomeWarnings

end Figram.Format

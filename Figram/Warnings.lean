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
snapshot). -/
def Doc.outcomeWarnings (d : Doc) : List String := Id.run do
  let c := d.toChart
  let ix := c.index
  let m := c.compile
  let mut ws : Array String := #[]
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
        | _ => pure ()
  return ws.toList

/-- Warnings about a document (only for a document without `check` errors). -/
def Doc.warnings (d : Doc) : List String := d.toChart.lintWarnings ++ d.outcomeWarnings

end Figram.Format

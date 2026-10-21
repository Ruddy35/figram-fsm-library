import Figram.Format

/-!
# Warnings about a document

What passes `Doc.check` but is most likely a designer's mistake: variants and
arrows that never fire, unreachable messages, timer loops that keep sibling
timers from firing (`Chart.lintWarnings`). Warnings affect neither `check` nor
the behaviour of the engine: a document that was valid stays valid.
`figram check` prints them to stderr with the prefix `warning:` and
exits with status 0.
-/

namespace Figram.Format

/-- Warnings about a document (only for a document without `check` errors). -/
def Doc.warnings (d : Doc) : List String := d.toChart.lintWarnings

end Figram.Format

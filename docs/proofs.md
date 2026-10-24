# What is proved

The semantics, the compiler, the document format and the engine protocol of Figram are
specified and proved in Lean 4 (`Figram/*.lean`). The same Lean code is compiled into the
`figram` and `figram-engine` executables and into `libfigram.a`. This page lists the theorems
that the project relies on, how the proofs are checked, and what the proofs do not cover.

## Pinned theorems

The statements of these 34 theorems are pinned: `./verify.sh` elaborates
[`tests/proofs/theorems.lean`](../tests/proofs/theorems.lean), prints the statement of each one
and requires it to equal the corresponding line of
[`tests/proofs/THEOREMS.expected`](../tests/proofs/THEOREMS.expected) character for character.
Changing a statement therefore requires a deliberate change of that file. Each theorem must also
depend on no axioms other than `propext`, `Quot.sound` and `Classical.choice`.

Notation: `c : Chart` is the designer's chart (the reference semantics, `Figram/Chart.lean`),
`m : Machine` is the flat compiled machine (`Figram/Core.lean`), `d : Doc` is a decoded
document (`Figram/Format.lean`), `J` is the JSON *model* (a Lean datatype, not text).

### Semantics and compiler

| Theorem | Meaning |
|---|---|
| `Figram.Chart.compile_correct` | If the compiled machine is well formed (`c.compile.wf`), one step of the compiled machine equals one step of the chart for **every** snapshot, event and assignment of condition values. |
| `Figram.Chart.run_correct` | The same for any sequence of inputs from any snapshot. |
| `Figram.Chart.session_correct` | The same for a whole session that starts from the initial snapshot. |
| `Figram.Chart.chart_run_valid` | If `c.compile.wf`, then after any non-empty sequence of inputs, from any snapshot (even a stale one), the user is on an existing message of the chart. |
| `Figram.Machine.step_state_valid` | For a well-formed machine, one step from any snapshot ends on a state of the machine. |
| `Figram.Machine.step_guard_local` | A step depends only on the conditions in `requiredGuards` for the current state and event: two condition assignments that agree on them give the same step. This justifies the `need` protocol. |
| `Figram.Chart.compile_eq_spec` | The fast index-based compiler builds exactly the same machine as the list-based specification `compileSpec` on which the core theorems are proved. |
| `Figram.Chart.wfDirect_eq` | `wfDirect`, computed on the chart without building the machine, equals `c.compile.wf`; `figram check` uses it. |
| `Figram.Chart.lint_nil_wf` | A chart without `lint` errors compiles to a well-formed machine. |
| `Figram.Machine.wfFast_eq` | The linear-time check `wfFast` that `Engine.load` runs equals `wf`. |

### Entering the bot with a first event

`start` with `event` ([format.md](format.md), section 7, decision 9) is `Machine.startWith`, defined
through `Machine.enterBot`; the chart's reference semantics is `Chart.startWith`.

| Theorem | Meaning |
|---|---|
| `Figram.Chart.startWith_correct` | If `c.compile.wf`, entering with an event on the compiled machine equals the chart's reference semantics, for every counter, event and assignment of condition values. |
| `Figram.Chart.sessionWith_correct` | The same for a whole session that starts by entering with an event. |
| `Figram.Machine.startWith_eq_step` | Entering with an event that chooses a variant is a step from the snapshot standing on the initial message (no timers, no places): the same snapshot, and the step's actions are the initial message's exit actions (cancelling timers that were never started) followed by those of `startWith`. |
| `Figram.Machine.startWith_initial` | If the chosen variant leads to the initial message, entering is exactly the first entry with the variant's actions in front: one entry, one `send` of the initial message. |
| `Figram.Chart.compile_initActions` | In a compiled machine the first-entry actions are the entry actions of the initial message (the last hypothesis of `startWith_initial`). |
| `Figram.Machine.startWith_unmatched` | Without a matching variant (or for a timer event), entering is the plain first entry. |
| `Figram.Machine.enterBot_state_valid` | For a well-formed machine, entering ends on a state of the machine. |
| `Figram.Machine.enterBot_guard_local` | Entering depends only on the conditions in `requiredGuards` of the initial message and the event. |

### Document format

| Theorem | Meaning |
|---|---|
| `Figram.Format.decode_encode` | For a document satisfying `DocWF`, encoding into the model `J` and decoding returns exactly the same document. |
| `Figram.Format.encode_injective` | Different documents satisfying `DocWF` are encoded differently (a corollary of `decode_encode`; false without `DocWF`). |
| `Figram.Format.decode_requires` | A decoded document requires only known extensions (`knownFeatures`): a document with an unknown semantic extension is rejected as a whole. |
| `Figram.Format.toChart_strip` | Removing all unknown keys and annotations (`strip`) does not change the chart, i.e. the bot's behaviour. |
| `Figram.Format.toChart_decls` | The description maps (`conditions`, `calls`, `variables`) do not affect the chart (true by definition, `rfl`). |
| `Figram.Format.check_roundtrip` | A document that passes `check` is encoded into the model `J` and decoded back unchanged. |

### Engine protocol

| Theorem | Meaning |
|---|---|
| `Figram.Engine.decSnapshot_encSnapshot` | An admissible snapshot (`SnapOK`: non-empty names, integers within 2^53 − 1) written into the model `J` is read back as exactly the same snapshot. |
| `Figram.Engine.step_snapOK` | For a well-formed machine with non-empty names (`namesOK`), a step from an admissible snapshot whose new `seq` does not overflow yields an admissible snapshot. |
| `Figram.Engine.handle_step` | A `step` request already decoded into the model (`handleObj`) — with a decodable snapshot, event and guards, no missing conditions and no `seq` overflow — replies exactly with the result of the proved `Machine.step`. |
| `Figram.Engine.reply_readable` | Under the hypotheses of `step_snapOK`, the snapshot in a `step` reply is read back by the engine unchanged. |
| `Figram.Engine.guards_irrelevant` | Conditions in `guards` that are not in `requiredGuards` do not affect the step. |
| `Figram.Engine.missing_nil_step` | If the engine asks for no conditions (`missing` is empty), the values of conditions that were not passed do not affect the step: answering `need` with exactly the listed conditions is enough. (That `need` lists no condition that cannot change the choice is a property of the algorithm, not a theorem.) |
| `Figram.Engine.startAt_snapOK` | For a well-formed machine with non-empty names, `start` with `seq` n yields an admissible snapshot whenever n + 1 does not exceed 2^53 − 1. |
| `Figram.Engine.startWith_snapOK` | The same for `start` with an event. |
| `Figram.Engine.handle_start` | A `start` request with an event, already decoded into the model — with a decodable `seq`, event and guards, no missing conditions and no `seq` overflow — replies exactly with the result of the proved `Machine.startWith`. |
| `Figram.Engine.missingStart_nil` | If `start` with an event asks for no conditions, the values of conditions that were not passed do not affect the entry. |

The exact statements are in [`tests/proofs/THEOREMS.expected`](../tests/proofs/THEOREMS.expected).
[`tests/proofs/Statements.lean`](../tests/proofs/Statements.lean) restates most of them in full
(as a reader would write them) and closes each by the theorem; it also pins
`Figram.Chart.start_correct` (the compiled machine starts like the chart),
`Figram.Machine.start_state_valid` (the initial state is a state of a well-formed machine) and
`Figram.Format.wfOK_sound` (the computable check `Doc.wfOK` implies `DocWF`).

### How the hypotheses are discharged

* `c.compile.wf` — `figram check` and `Engine.load` reject a document whose chart fails `lint`
  (`lint_nil_wf`), and `Engine.load` additionally checks `wfFast && namesOK` on the built
  machine (`wfFast_eq`); the test-mode mutants are checked the same way before they run.
* `DocWF` — checked by `check` in computable form (`Doc.wfOK`, `wfOK_sound`).
* `SnapOK` — the engine's snapshot decoder `decSnapshot` accepts only admissible snapshots
  (proved as `Figram.Audit.decSnapshot_sound`, see below).
* Decision 7 of the semantics (a timer firing needs the current token) is enforced by the request
  decoder `Engine.decEvent`: in the core model the event `.named "timer:x"` is accepted without a
  token, and the core theorems do not exclude it (`Figram.Audit.timer_key_needs_token` closes the
  gap at the protocol level).

### Supplementary lemmas

[`tests/proofs/FigramAudit/Glue.lean`](../tests/proofs/FigramAudit/Glue.lean) (library
`FigramAudit`, built with `lake build FigramAudit`; not part of `./verify.sh`, built by the CI
suite `proofs`)
connects the product theorems end to end, among others:

* `Figram.Audit.check_compile_correct` — a document that passed `check` is compiled correctly,
  without a separate `wf` hypothesis;
* `Figram.Audit.load_sound` — every machine accepted by `Engine.load` comes from a document that
  passed `check` and satisfies `wf` and `namesOK`;
* `Figram.Audit.engine_step_is_reference` — a `step` request to a loaded engine (at the level of
  the JSON model) computes the reference semantics `Chart.step` of the loaded document;
* `Figram.Audit.start_snapOK`, `Figram.Audit.restart_snapOK` — `start` and `restart` produce
  admissible snapshots;
* `Figram.Audit.decSnapshot_sound`, `Figram.Audit.step_reply_readable` — every decoded snapshot
  is admissible, so a `step` reply of a loaded engine is always readable back.

## How the proofs are checked

`./verify.sh` runs two independent gates ([testing.md](testing.md)):

1. **Pinned statements and axioms** — `tests/proofs/theorems.lean` prints `#print axioms` and the
   statement of each pinned theorem; the axioms must be a subset of `propext`, `Quot.sound`,
   `Classical.choice`, and the statements must equal `tests/proofs/THEOREMS.expected`. The Lean
   sources (`Figram.lean`, `Main.lean`, `EngineMain.lean`, `Figram/*.lean`) must not contain
   `axiom`, `sorry`, `admit`, `native_decide`, `implemented_by`, `@[extern`, `skipKernelTC`,
   `ofReduceBool` or `ofReduceNat`. Without `implemented_by` and `@[extern]`, the compiled code
   of every proved definition is that definition itself.
2. **Strict gate** — [`tests/proofs/strict_gate.sh`](../tests/proofs/strict_gate.sh):
   * a static scan of the sources for constructs that bypass or weaken checking (`sorry`,
     `admit`, `axiom`, `native_decide`, `ofReduceBool`, `ofReduceNat`, `trustCompiler`,
     `debug.skipKernelTC`, `run_elab`, `run_cmd`, `run_meta`, `modifyEnv`, `addDeclCore`,
     `unsafe`, `@[csimp]`);
   * the list of every `theorem` in `Figram/*.lean` is generated from the sources and must equal
     the tracked pin [`tests/proofs/theorems.txt`](../tests/proofs/theorems.txt) (159 theorems);
     `#print axioms` is run for each of them, the number of answers must match, and only the
     three standard axioms are allowed;
   * [`tests/proofs/AxiomGate.lean`](../tests/proofs/AxiomGate.lean) repeats the axiom check from
     the Lean environment for every theorem, definition and opaque constant of the `Figram`
     modules (including generated lemmas) and fails if a project module declares an `axiom`;
   * [`tests/proofs/Statements.lean`](../tests/proofs/Statements.lean) must elaborate (pinned
     statements, see above);
   * `lake env leanchecker Figram` re-checks every declaration of every `Figram` module with the
     kernel, which catches environment manipulation that `#print axioms` cannot see.

`tests/proofs/test_strict_gate_clean.sh` checks that the strict gate never rewrites tracked files
and that the tracked theorem list acts as a pin (CI runs it).

The other files in `tests/proofs/` are not part of `./verify.sh`; the CI suite `proofs` runs them
after `test_strict_gate_clean.sh`, each with its expected outcome:

* [`tests/proofs/Counterexamples.lean`](../tests/proofs/Counterexamples.lean) shows concrete
  charts and documents on which the theorems' conclusions fail once a hypothesis is dropped, and
  where the proved model differs from the text and protocol layers; `StatementsNegative.lean` and
  `CounterexamplesNegative.lean` are negative controls that Lean must reject with the number of
  errors stated in their header (the theorems without their hypotheses, the opposite claims);
* `TextRoundTrip.lean` checks with `#guard` that documents with extreme numbers in annotations
  are printed into text that reads back as the same document;
* `GlueAxioms.lean` prints the axioms of the supplementary lemmas (only the three standard axioms
  are allowed) and `OpaqueDeps.lean` lists the theorem statements that depend on `partial`
  definitions; both need the `FigramAudit` library.

By hand:

```sh
lake build FigramAudit
for f in Counterexamples TextRoundTrip GlueAxioms OpaqueDeps; do lake env lean tests/proofs/$f.lean; done
lake env lean tests/proofs/StatementsNegative.lean       # must fail with 2 errors
lake env lean tests/proofs/CounterexamplesNegative.lean  # must fail with 4 errors
```

## What the proofs do not cover

* **The JSON text layer** (`Figram/FormatIO.lean`: `parse`, `print`, `renderCompact`). These are
  `partial` definitions, opaque to the logic; all format and protocol theorems are stated on the
  JSON model `J`, not on text. The text round trip is tested: by differential fuzzing against
  Python `json` in `./verify.sh`, by the formats suite, and by `tests/proofs/TextRoundTrip.lean`
  (CI suite `proofs`).
* **The string-level request handler** `Engine.handle`: the strict field check, the size limit
  and UTF-8 validation run before the proved `handleObj` and can only reject requests; they are
  tested by `tests/engine/protocol_edges.py` and the formats suite.
* **The CLI, the C shim** (`ffi/*.c`, `ffi/*.cpp`), **the Go and Python clients.** They are
  covered by the test suites in [testing.md](testing.md).
* **The texts of `check` diagnostics.** The decisive checks are `compile.wf` and `Doc.wfOK`, which
  are hypotheses of the theorems; the messages themselves are not specified.
* **Code outside Figram:** the developer's conditions and actions, the host, and the trusted
  base — the Lean kernel, the Lean compiler and runtime, the C compiler and the system libraries.

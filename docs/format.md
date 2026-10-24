# Figram document format (version 1) and engine protocol (version 2)

This document specifies two formats:

* the **bot document** (sections 1–6) and its execution semantics (section 7): the whole bot as
  one JSON value, written by an editor and read by `figram` and `figram-engine`;
* the **user snapshot and the engine protocol** (section 8): what a host stores per user and
  the line-delimited JSON protocol of `figram-engine` and of the embedded library.

Properties marked *proved* and the theorems named below are theorems of the Lean sources
(`Figram/FormatProofs.lean`, `Figram/EngineProofs.lean`, `Figram/Correct.lean`,
`Figram/LintProofs.lean`); [proofs.md](proofs.md) lists the main ones with their exact
statements.
Machine-readable schemas: [`schemas/figram.schema.json`](../schemas/figram.schema.json) and
[`schemas/snapshot.schema.json`](../schemas/snapshot.schema.json) (JSON Schema 2020-12).

## 1. Setting

**Model** `M` — the set of documents: a tree of sections and messages, arrows with ordered
variants, conditions, actions and variables with text descriptions, and timers. In Lean this
is the type `Format.Doc`.

**Format** — a pair of functions: `encode : M → JSON` and `decode : JSON → M ∪ {error}`.

**Canonical form** — the image of `encode`, i.e. the JSON an editor writes (`figram fmt`
prints it). A reader must also accept non-canonical encodings of the same document: other
key order, explicitly written default values.

## 2. Criteria

**C1. Completeness.** `decode(encode(d)) = d` for every well-formed `d`. Nothing is lost,
including unknown keys and their order. *Proved:* `decode_encode`.

**C2. Unambiguity.** Different documents are encoded differently: `encode` is injective.
*Proved:* `encode_injective` (a corollary of C1).

**C3. Minimality.**
(a) The JSON contains no data that can be computed from the rest.
(b) Default values are omitted from the canonical form.
(c) A document has exactly one canonical encoding. A document is an *ordered* structure: the
key order of maps (`children`, `on`, `conditions`, …) is kept so that the designer's order is
not lost. Two documents that differ only in the key order of a map are therefore encoded
differently. Key order does not affect the bot's behaviour except for `timers`: the order of a
message's timers is the order of the `start_timer` and `cancel_timer` actions in an engine
reply, and therefore of the action positions used in a host's idempotency keys
([host-contract.md](host-contract.md), section 1). For hashing or "semantic" comparison, sort
the maps by key first — all maps except `timers` if the action order matters. `figram fmt`
does not sort.

**C4. Structural correctness.** Let `V` be the correctness conditions that the JSON structure
does not enforce and that must be checked separately. The format is chosen to make `V`
minimal: whatever the shape of the data can make impossible, it does.

**C5. Extensibility.**
(a) Unknown keys are read, kept, and do not affect behaviour. *Proved:* `toChart_strip`;
keeping them is a special case of C1. An unknown key must start with `x-` (an annotation):
`figram check` and the engine reject a document with any other unknown key (section 5).
(b) An extension that changes meaning is declared in `requires`; a reader that does not know
it rejects the whole document instead of misreading it. *Proved:* `decode_requires`.
(c) A new field has a default value equal to the old behaviour, so old documents mean the
same to a new reader.

## 3. Derivation

Each row is a decision, the rejected alternative, and what the decision gives in terms of the
criteria. The last column counts the conditions of `V` eliminated compared with the
alternative.

| # | Decision | Alternative | What it gives | −V |
|---|---|---|---|---|
| 1 | Nested nodes: a section contains `children` | A flat list of nodes with a `parent` reference | A tree by construction: no cycles, exactly one parent, the parent exists and is a section | 4 |
| 2 | Nodes are an object "name → node" | An array of nodes with an `id` field | Sibling names are unique by construction; no `id` field (C3a) | 1 |
| 3 | The bot is the root section `bot` | A `root` field referencing a node | No "root exists" condition; global commands such as `/start` are ordinary arrows of the root | 1 |
| 4 | Arrows live in the node: `on` is an object "event → variants" | A separate list of arrows with a `src` field | No "source exists" condition; a (node, event) pair has one place, so the variant order is unambiguous (C3c) | 2 |
| 5 | Arrays only where order is meaning: variants of an arrow and actions | Arrays everywhere | Other collections are objects, without uniqueness conditions and without a false order (C3c; exception — `timers`, whose key order is the order of timer actions) | — |
| 6 | One target field `to`; "resume where the user left off" is the flag `resume` | Two fields `to` and `back` | Exactly one target by construction | 1 |
| 7 | The kind of a node is given by the presence of `children` | A `kind` field | No computable field (C3a) and no "kind: message, but has children" mismatch | 1 |
| 8 | One condition per variant: `"if": "name"` | A list of conditions | The designer writes a condition as text, and the text can say "A and B"; a list would be a second way to write the same thing (C3c) | — |
| 9 | Texts of conditions, actions and variables live in the maps `conditions`, `calls`, `variables`; arrows use only names | Text inside the arrow | A text is written once even if the condition is used by ten arrows; two texts for one condition are impossible | 1 |
| 10 | Timers live inside the message: `timers` | A separate list of timers with a `node` field | A timer cannot point to a missing node; the schema forbids timers on sections | 2 |
| 11 | Defaults are omitted: `on`, `timers`, `if`, `resume: false`, `do`, `requires`, description maps | All fields always written | C3b; a reader also accepts an explicitly written default | — |
| 12 | A number in the model is an exact `m · 10^e`; format fields are checked by value | Integers only | Extensions can store any number (coordinates, zoom); `7200000`, `7.2e6` and `7200000.0` are the same delay | — |

**Numbers.** The number grammar is RFC 8259; the absolute value of an exponent in the text is
at most 1000 (protection against inputs such as `1e1000000000`). Integer fields of the format
(timer delays) range from 0 to 2^53 − 1, which JavaScript, Go, Rust and Python represent
exactly. A number with a large negative exponent is printed in exponent notation while the
absolute exponent stays at most 1000; when it would be larger (only possible for numbers with a
fractional part, such as `1.5e-1000`), the number is printed positionally, so that the printed
text can be read back. The model
does not distinguish negative zero: `-0.0` is written as `0.0` (in annotations too — the sign
of a zero is lost after `figram fmt`).

Integer fields (delays, `figram`, and in the snapshot `v`, `seq` and timer tokens) are checked
exactly, by the value `m · 10^e`: `1e-400`, `9007199254740990.5` and `1.0000000000000001` are
not integers, and both `figram check` and the engine reject them. JSON Schema validators that
read numbers as doubles (for example Python `jsonschema`) round such values to the nearest
double and accept them: the schema lets through non-integers that differ from an allowed
integer by less than one double step. The opposite discrepancy (the engine accepts what the
schema forbids) does not occur in the table of `tests/formats/c_schema_table.py`. The decoder,
not the schema, is authoritative.

### What remains in `V`, and why it cannot be smaller

After decisions 1–12, five semantic conditions remain, and `figram check` checks them:

- **V1.** Node names are unique in the whole tree and non-empty.
- **V2.** The `initial` of a section is a direct child of that section.
- **V3.** Every `to` exists; with `resume: true` it is a section.
- **V4.** Every `if` is described in `conditions`, every `do` entry in `calls`.
- **V5.** An event `timer:<name>` refers to an existing timer.

In addition, `check` verifies what holds automatically for JSON text but can be violated by a
document built programmatically (names in maps are non-empty and unique, annotations do not
collide with format keys), that every unknown key starts with `x-` (section 5), the value
ranges of integer fields (a timer delay at most 2^53 − 1), and, as its last step, that the
chart compiles to a well-formed machine (section 7). A document that passes `check` is
guaranteed to be encoded into the JSON model `J` and decoded back (theorem `check_roundtrip`);
the round trip through text (`print`/`parse`) is not proved and is covered by tests.

**Warnings.** For a document without errors, `figram check` also looks for probable designer
mistakes and prints them to stderr, one per line; the exit status stays 0 and the document
stays valid (`Doc.warnings`; warnings do not affect the engine):

- a variant after a variant without a condition, or with a condition already used earlier in
  the same arrow, can never fire;
- a message is unreachable from the start of the bot;
- "resume" into a section is drawn only from inside that section (it always leaves the user
  where they are);
- an arrow `timer:X` sits on a message that does not start timer X, or on a section in which no
  message starts it (a firing is accepted only by the message that started the timer);
- a timer self-loop on its own message restarts the message's other timers whose delay is not
  smaller — they never fire.

V1 and V3–V5 are references between parts of the document. A JSON value is a finite tree,
while the arrow graph of a bot has cycles (menu → order → "Cancel" → menu). Arrows therefore
cannot be expressed by nesting and must use names, and every reference by name carries the
condition "the name exists and is unambiguous". V1, V3, V4 and V5 cannot be removed in any
JSON format.

V2 could be removed by making the first child the initial one. The order of `children` would
then carry meaning, children would have to be an array with a name field, and the sibling
uniqueness condition would come back. That trades one condition for another; the chosen
variant keeps all node collections as objects.

## 4. Grammar

```text
Document = { "figram": 1,
             "requires"?:   [Name],
             "conditions"?: { Name: Text },
             "calls"?:      { Name: Text },
             "variables"?:  { Name: Text },
             "bot":         Section,
             …annotations }

Node     = Section | Message                    (Section ⇔ has "children")

Section  = { "initial": Name,
             "on"?:       Arrows,
             "children":  { Name: Node },
             …annotations }

Message  = { "on"?:     Arrows,
             "timers"?: { Name: Integer 0 … 2^53−1 },   (delay in milliseconds)
             …annotations }

Arrows   = { Event: [Variant] }                 (the first matching variant wins)

Variant  = { "if"?:     Name,                   (absent — "otherwise / always")
             "to":      Name,
             "resume"?: true,                   (resume where the user left off)
             "do"?:     [Name],
             …annotations }
```

The structural part is also available as [`schemas/figram.schema.json`](../schemas/figram.schema.json).

**Nesting limit.** JSON nesting is at most 256 levels of objects and arrays (the document
itself is level 1); requests to the engine have the same limit. Each nested section takes two
levels (`children` and the section itself), so `bot` can hold at most 126 levels of nested
sections when the deepest message has no arrows, and 124 when it has an arrow with actions;
nested annotations reduce these numbers. A deeper document is rejected when it is read
(`figram check`, engine load). The schema does not express this limit.

All names (of nodes, events, timers, conditions, actions, variables) are non-empty and unique
within their map.

A timer firing arrives as the event `timer:<name>`; the prefix `timer:` is reserved. The format
does not restrict other event names; the examples use the conventions `btn:…`, `cmd:…`,
`msg:…`, `payment:…`.

Example: [`examples/pizza.bot.json`](../examples/pizza.bot.json).

## 5. Extension rules

**Annotations** are everything that does not change the behaviour of the machine: canvas
coordinates, message texts, button labels, comments. They are written under keys with the
prefix `x-` (`x-ui`, `x-content`, `x-note`) in the objects with fixed keys: document, section,
message, variant. In maps (`children`, `on`, `timers`, description maps) the keys are names,
so maps carry no annotations. Format keys never start with `x-`, so annotations never collide
with future format fields. A reader that rewrites a document must keep all unknown keys.
Example: [`examples/annotations.bot.json`](../examples/annotations.bot.json).

An unknown key without the `x-` prefix is an error of the document. The decoder still reads
and keeps it (C1: `figram fmt` prints it back), but `figram check` and the engine reject the
document with the error ``unknown key `…` (annotation keys start with `x-`)``; the schema
([`schemas/figram.schema.json`](../schemas/figram.schema.json)) allows only `x-` keys besides
the format keys. A misspelt optional field is therefore reported instead of silently changing
the bot: without this rule `"tiemrs"` in a message would leave the message without timers and
`"iff"` in a variant would leave the variant without a condition. The rule covers the
document, sections, messages and variants; inside the value of an annotation any keys are
allowed. A key in `children` is a node name, not an annotation: `"x-ui": {}` there is a new
message named `x-ui`. A reader that does not know a format field added later rejects a document
that uses it.

**Semantic extension** — a new field or value that changes the bot's behaviour. It is added
as follows:

1. The extension has a name, for example `section-timers` (timers on sections).
2. A document that uses it writes `"requires": ["section-timers"]`.
3. A reader that does not know `section-timers` rejects such a document as a whole (C5b).
   Without this rule an old reader would silently ignore the timer and the bot would behave
   differently from what the designer drew.
4. The default behaviour (field absent) equals the behaviour of version 1 (C5c).

Version 1 defines no extensions (`knownFeatures` is empty), so a document with a non-empty
`requires` is rejected.

A **new major version** (`"figram": 2`) is needed only for a change that cannot be expressed
this way, for example a change of the meaning of an existing field.

## 6. What is proved and what is not

| Property | Theorem |
|---|---|
| C1: encoding then decoding returns the same document | `decode_encode` |
| C2: different documents give different JSON | `encode_injective` |
| C5a: unknown keys do not affect behaviour | `toChart_strip` |
| C5b: an accepted document requires only known extensions | `decode_requires` |
| Descriptions of conditions, actions and variables do not affect behaviour | `toChart_decls` |
| A document that passes `check` is encoded into the model `J` and decoded back | `check_roundtrip` |

Hypotheses of the theorems (`DocWF`): annotations do not use format keys, `requires` holds only
known extensions, `bot` is a section. `check` verifies them in computable form (`Doc.wfOK`,
theorem `wfOK_sound`).

Not covered by proofs:

- the text layer (`Figram/FormatIO.lean`, about 360 lines): JSON parsing and printing. It
  rejects duplicate keys, leading zeros and trailing commas, and is tested differentially
  against Python `json` (see [testing.md](testing.md));
- agreement of the schemas with the decoder: checked on the examples and by the table in
  `tests/formats/c_schema_table.py`; the schema is wider than the decoder for doubles
  (section 3, "Numbers") and does not express the nesting limit (section 4);
- the rule that an unknown key starts with `x-` (section 5): checked by `#guard`s in
  `Figram/Examples.lean` and by the formats suite;
- C3 and C4: these are properties of the derivation above, not theorems.

## 7. Execution semantics

The designer's chart (`Figram/Chart.lean`, `Chart.step`) is the reference semantics; the
compiler (`Figram/Compile.lean`) turns it into a flat machine (`Figram/Core.lean`,
`Machine.step`), and `Chart.compile_correct` proves that both take the same step. A step is a
pure function `Machine.step : Machine → Snapshot → Event → HostEnv → Snapshot × List Action`.
The chart adds three simplifications on top of messages, events, arrows with variants and
actions:

* **sections** — nesting; an arrow drawn on a section works on every message inside it; an
  arrow into a section leads to its initial message;
* **"resume where the user left off"** — an arrow (`"resume": true`) that leads into a section
  at the message where the user stopped;
* **timers** — a timer on a message.

"Resume" is an operation of the core: the place is read from the snapshot during the step, so
"resume" adds one entry per section to the compiled machine instead of a copy of the section for
every remembered place. The arrows of a section are copied into every message inside it, so
the number of machine rows can reach the number of messages times the number of section
events ([testing.md](testing.md), "Performance").

Decisions of the specification:

1. **Bubbling.** Variants are collected from the message up through the enclosing sections;
   the first variant whose condition holds wins. If the message has an arrow for the event but
   all its conditions are false, the event goes up to the enclosing section.
2. **Action order.** Exit (cancel the timers of the message being left and every other timer
   in the snapshot — leftovers of a previous document version) → actions of the variant →
   entry (remember the place, start timers, send the message). The host sees `cancel_timer`,
   `call`, `start_timer`, `send` actions in this order.
3. **Self-loops.** An arrow to the same message sends it again and restarts all timers of that
   message. A timer loop is no exception: a `tick` loop every second also restarts a sibling
   10-second `timeout`, which then never fires while the loop runs
   ([host-contract.md](host-contract.md), section 3).
4. **Remembering places.** The place is remembered on entry into a message, for every enclosing
   section into which at least one "resume" arrow leads.
5. If the remembered message no longer exists in the document or is no longer inside that
   section, "resume" leads to the section's initial message.
6. **Unmatched events.** An event without a matching arrow is ignored: the snapshot does not
   change. This includes an accepted timer firing without an arrow or with all conditions
   false: the timer's entry stays in the snapshot until the user leaves the message, and
   leaving emits `cancel_timer` for it ([host-contract.md](host-contract.md), section 3).
7. **Timer tokens.** A timer firing is accepted only with the token of the current run and only
   on the message that owns the timer.
8. **Recovery.** If the snapshot's message no longer exists in the document (or `at` names a
   section), the user starts the bot again: the timers of the old place are cancelled first,
   the remembered places are kept, and the event is processed as in decision 9, as if the user
   stood on the initial message — a chosen variant leads straight to its target (the
   remembered places are used by "resume"), otherwise the user enters the initial message
   (except the places overwritten by its entry, decision 4). A timer event chooses no variant:
   for it recovery is the plain start over. Recovery happens for any event, including a timer
   event with a stale token.
9. **First event.** A new user can enter the bot with the update that brought them (`start`
   with `event`, section 8): the event is matched as if the user stood on the initial message,
   by the arrows of the initial message and then of its sections up to the root (decision 1).
   The initial message itself is neither entered nor left: if a variant is chosen, the actions
   are the variant's actions and the entry into its target, so the initial message is not sent,
   its timers are not started and it is not remembered in `resume`; a variant into the initial
   message enters it once, which is exactly the first entry with the variant's actions in front.
   Not remembering the initial message changes nothing: it is the initial message of every
   section on its path, where "resume" would lead without a remembered place. If no variant is
   chosen, this is the plain first entry. A timer event cannot start a user (a new user has no
   timers). An arrow of the initial message or of a section around it therefore also fires
   for a new user, for example a button of an old message pressed after the host lost the
   user's snapshot.

The correctness theorems assume `c.compile.wf`. `figram check` establishes it on the chart,
without building the machine (the last check of `lint`; theorems `wfDirect_eq`,
`lint_nil_wf`), and `Engine.load` checks it again on the built machine with the linear check
`wfFast` (`wfFast_eq`). Decision 7 is enforced by the request decoder (`Engine.decEvent`): in
the core model the event `.named "timer:x"` is accepted without a token, and the theorems do
not exclude it.

## 8. Snapshot and engine protocol, version 2

The document is the exchange format between the editor and the developer. The snapshot and the
protocol are the exchange format between the engine and the host. The flat machine into which
a document is compiled is an internal representation, not an exchange format: every engine
(the `figram-engine` process, the embedded library) builds it from the document with the
proved compiler.

### User snapshot

What the host stores between events:

```text
Snapshot = { "v"?:      1,                       (version; absent — 1)
             "at":      Name,                    (current message)
             "resume"?: { Section: Message },    (remembered places)
             "timers"?: { Timer: Token },        (active timers)
             "seq"?:    Integer }                (number of the last transition; absent — 0)
```

Tokens and `seq` are integers from 0 to 2^53 − 1; `at`, the keys and values of `resume` and
the keys of `timers` are non-empty strings. The snapshot is strict: the engine accepts exactly what
[`schemas/snapshot.schema.json`](../schemas/snapshot.schema.json) allows; an unknown field or
an unknown version is an error (such a snapshot was written by a newer engine). `seq` grows by
1 with every transition; it is the token of the timers started in that transition and the
basis of the host's idempotency keys ([host-contract.md](host-contract.md)).

Integers are compared exactly, by value (`5`, `5.0` and `5e0` are the same number). Schema
validators that read numbers as doubles are wider than the engine: they accept non-integers
that differ from an integer by less than one double step (`"seq": 1e-400`,
`9007199254740990.5`), while the engine rejects such a snapshot (`bad_snapshot`). Snapshots
produced by the engine are always integral.

### Protocol

One JSON line (UTF-8) per request and one per reply, with the same `id`. Replies come strictly
in request order: the reply to request *n* is written before the reply to request *n* + 1 (a
host that receives a reply with `id: null` can rely on this). `id` is any JSON value and is
returned by value (`-0` becomes `0`); a request without `id` gets a reply with `id: null`.

| Request | Reply |
|---|---|
| `{"op": "hello"}` | `{"protocol": 2, "states": N}` — N is the number of messages of the loaded document |
| `{"op": "start", "seq"?: N}` | `{"snapshot": …, "actions": […]}` — first entry; the first transition gets `seq` N + 1 (without `seq` — 1) |
| `{"op": "start", "seq"?: N, "event": "cmd:start", "guards"?: {…}}` | as above, or `{"need": [conditions]}` — first entry with the user's first event (decision 9 of section 7) |
| `{"op": "restart", "snapshot": …}` | `{"snapshot": …, "actions": […]}` — start over without resetting the counter |
| `{"op": "step", "snapshot": …, "event": "btn:x", "guards"?: {…}}` | `{"snapshot": …, "actions": […], "recovered"?: true}` or `{"need": [conditions]}` |
| `{"op": "step", …, "event": "timer:<name>", "token": n}` | as above |

Requests are strict: only the fields in the table and `id` are allowed (`hello` — `id`, `op`;
`start` — also `seq`, `event`, `guards`; `restart` — also `snapshot`; `step` — also
`snapshot`, `event`, `guards`, `token`). Any other field, such as the typo `gaurds`, is a
`bad_request` error. `event` is a non-empty string. `token` is allowed only with a timer event
(`timer:<name>` with a non-empty name) and is required there; `start` takes no timer event and
no `token`, and takes `guards` only with `event`. `token` and the `seq` of `start` are integers
from 0 to 2^53 − 1 by value (`5`, `5.0`, `5e0`; `-0` is 0). `guards` is an object
"condition → true/false"; extra conditions do no harm, but every value must be a boolean.

The protocol version in `hello` grows when requests are added: version 2 added `start` with
`event` and `guards`. A host that sends them checks `"protocol": 2` (or higher) first.

Actions for the host, in execution order:
`{"send": message}`, `{"call": action}`,
`{"start_timer": name, "ms": delay, "token": token}`, `{"cancel_timer": name}`.

A session with [`examples/pizza.bot.json`](../examples/pizza.bot.json):

```sh doctest
$ printf '%s\n' \
>   '{"id":1,"op":"hello"}' \
>   '{"id":2,"op":"start"}' \
>   '{"id":3,"op":"step","snapshot":{"at":"menu","seq":1},"event":"btn:order"}' \
>   '{"id":4,"op":"step","snapshot":{"at":"cart","seq":3},"event":"btn:checkout"}' \
>   '{"id":5,"op":"step","snapshot":{"at":"cart","seq":3},"event":"btn:checkout","guards":{"cart_not_empty":true}}' \
>   | figram-engine examples/pizza.bot.json
{"id":1,"protocol":2,"states":11}
{"id":2,"snapshot":{"at":"menu","seq":1},"actions":[{"send":"menu"}]}
{"id":3,"snapshot":{"at":"catalog","resume":{"order":"catalog"},"seq":2},"actions":[{"send":"catalog"}]}
{"id":4,"need":["cart_not_empty"]}
{"id":5,"snapshot":{"at":"address","resume":{"order":"address"},"seq":4},"actions":[{"cancel_timer":"cart_reminder"},{"send":"address"}]}
```

Rules of a step:

1. If the snapshot's message is not in the document, the user starts the bot again: all of the
   user's timers are cancelled, then the event is processed as for `start` with that event
   (decisions 8 and 9 of section 7, the rules of `start` with an event below), with the
   remembered places kept and `seq` continued; `need` can be asked as for a step. The reply
   carries `"recovered": true`.
2. A timer event is ignored (the snapshot does not change, no actions) if its token differs
   from the current one or if the timer does not belong to the current message (this happens
   only with a snapshot written under a previous document version). Leaving a message cancels
   its timers and every other timer of the snapshot.
3. If a variant needs conditions that are not in `guards`, the reply is `{"need": [...]}`. The
   host computes these conditions as [host-contract.md](host-contract.md) requires (section 1,
   step 3: on data where all `call` actions committed by earlier steps of this user are applied
   and the actions of the current step are not) and repeats the request with the same snapshot
   and event, adding them to `guards`. `need` contains only conditions that can affect the
   choice: the conditions of the variants in order (the message's variants, then those of the
   enclosing sections) up to the first variant without a condition or the first condition
   given as `true`; conditions already given are not repeated. `need` can therefore be shorter
   than before: a host must not assume that it lists all conditions of the arrow. When `need`
   is empty, the values of conditions that were not given do not affect the step (theorem
   `missing_nil_step`); extra conditions in `guards` do not affect it either
   (`guards_irrelevant`).
4. An event without a matching arrow is ignored.
5. If the new `seq` would exceed 2^53 − 1, the reply is the error `seq_overflow`: the engine
   never produces a snapshot it could not read back (the same holds for `restart` and for
   `start` with `seq` 2^53 − 1). At one transition per millisecond the limit is about
   285 000 years away.

Rules of `start` with an event (decision 9 of section 7):

1. The event is matched as if the user stood on the initial message: by the arrows of the
   initial message, then of its sections up to the root.
2. If a variant needs conditions that are not in `guards`, the reply is `{"need": [...]}`, as
   in rule 3 of a step; the host repeats the same request with them in `guards`. `need` comes
   before the `seq_overflow` check.
3. If a variant is chosen, the actions are its actions and the entry into its target; the
   initial message is not sent and its timers are not started. Otherwise the reply is that of
   `start` without an event.
4. The reply is one result, and its transition gets `seq` N + 1, as for `start` with `seq` N
   (`seq_overflow` likewise).

```sh doctest
$ printf '%s\n' \
>   '{"id":1,"op":"start","event":"btn:go"}' \
>   '{"id":2,"op":"start","event":"btn:go","guards":{"is_vip":true}}' \
>   '{"id":3,"op":"start","event":"cmd:start"}' \
>   | figram-engine examples/features.bot.json
{"id":1,"need":["is_vip"]}
{"id":2,"snapshot":{"at":"vip","seq":1},"actions":[{"send":"vip"}]}
{"id":3,"snapshot":{"at":"home","timers":{"idle":1},"seq":1},"actions":[{"start_timer":"idle","ms":60000,"token":1},{"send":"home"}]}
```

The arrow `cmd:start` of the root section leads to the initial message `home`, so request 3 is
exactly `start`; with the event `btn:go` the user goes straight to `vip`, and `home` is neither
sent nor has its timer started.

`restart` cancels the snapshot's timers, clears the remembered places and performs the
first-entry actions, but continues the `seq` counter. The tokens of new timers are therefore
greater than any old token, a late event of an old timer never matches a new one, and the
host's idempotency keys (`user:seq:index`, [host-contract.md](host-contract.md)) do not repeat.
Starting a user over with `start` without `seq` (counter from zero) is safe only if the user
never had timers or actions: otherwise new actions get the keys of old ones and are dropped as
duplicates ([host-contract.md](host-contract.md), section 4). A host that starts an existing
user over with `start` passes in `seq` the user's persistent high-water mark — the largest
`seq` ever issued to that user.

### Errors

A request error is `{"id": …, "error": "text", "code": "code"}` with exactly these three
fields; the snapshot is not changed. The text is for humans and may change between versions;
hosts decide by `code`:

| `code` | When |
|---|---|
| `parse_error` | the line is not JSON, is not UTF-8, has a duplicate key, nesting deeper than 256, an exponent with absolute value above 1000, or text after the JSON value |
| `too_large` | the request line is longer than the limit |
| `bad_request` | unknown operation; unknown, missing or mistyped field; invalid `event`, `token`, `seq` or `guards`; the request is JSON but not an object |
| `bad_snapshot` | the snapshot in the request is not accepted (rules of "User snapshot") |
| `seq_overflow` | the new `seq` would exceed 2^53 − 1 (rule 5) |

Repeating a request that failed with `bad_request`, `bad_snapshot`, `seq_overflow` or
`parse_error` gives the same error ([host-contract.md](host-contract.md), section 5).

If a line cannot be parsed as JSON (`parse_error`) or is longer than the limit (`too_large`),
and it starts with `{"id":` followed by a scalar value (string, number, `true`, `false`,
`null`) and then `,` or `}` (JSON whitespace is allowed between all parts), the reply carries
that `id`; otherwise `id: null`:

```sh doctest
$ printf '%s\n' '{"id":7,"op":"hello"} x' '{"id":8,"op":"step","snapshot":{"at":"cart"},"event":"btn:checkout","gaurds":{}}' '[1]' \
>   | figram-engine examples/pizza.bot.json
{"id":7,"error":"extra text after the JSON value","code":"parse_error"}
{"id":8,"error":"unknown request field `gaurds`","code":"bad_request"}
{"id":null,"error":"request must be an object","code":"bad_request"}
```

The `figram-engine` process reads requests as bytes:

* a line longer than the limit (excluding the final `\n`; by default 1 MiB = 1 048 576 bytes,
  `figram-engine --max-request BYTES`, 0 — no limit) gets a `too_large` reply; the engine keeps
  at most the first 64 KiB of such a line (to find the `id`) and goes on with the next line;
* a line that is not valid UTF-8 gets `parse_error` (bytes are not replaced with U+FFFD; a bot
  document with invalid UTF-8 is rejected as well);
* a line consisting only of spaces, tabs and `\r` is skipped without a reply.

The embedded library (C, Go) takes one request per call, with the same limit and the same
UTF-8 check; an empty request gets `parse_error` (the process skips an empty line without a
reply). The library produces these three errors itself, before parsing, always with
`id: null` (no `id` echo: the reply already belongs to the call); all other errors, including
`parse_error` for invalid JSON, are produced as in the process, with the `id` echo. See
[embedding.md](embedding.md).

### Test mode

The test mode measures the strength of the tests: `figram-engine --mutants <bot.json>` lists
the mutations of the compiled machine, and `figram-engine --mutate <mutation> <bot.json>` runs a
deliberately broken engine. Both start only with `FIGRAM_ALLOW_MUTANTS=1` in the environment
(otherwise exit status 2). The broken engine prints a warning to stderr, rejects a mutation
whose number or name does not exist in the machine (exit status 2), and its `hello` reply
carries the field `"mutant": "<mutation>"`.

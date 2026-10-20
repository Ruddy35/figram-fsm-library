# Engine protocol v1 — agreed changes for the fix wave (all agents must follow this exactly)

Owner of the engine side: fix-protocol. Owners of consumers: fix-client (figram_client.py), fix-ffi (C ABI,
go-example checkReply), fix-protocol also updates `ffi/python-example/protocol.py` and the harness tests.
Protocol stays version 1 (`hello` → `"protocol": 1`); changes are additive or tighten undocumented leniency.

1. **Error code.** Every error reply carries a machine code:
   `{"id": <id|null>, "error": "<human text>", "code": "<code>"}` with codes:
   `parse_error` (not JSON, invalid UTF-8, duplicate key, depth > 256, exponent > 1000, trailing text),
   `too_large` (request line longer than the limit, see 5),
   `bad_request` (unknown/missing/ill-typed field, unknown op, bad event/token/guards),
   `bad_snapshot` (snapshot fails `decSnapshot`),
   `seq_overflow` (existing semantics; the old error text "seq_overflow" is kept as `error` text too).
   Validators must accept exactly the keys {id, error, code} for an error reply.
2. **id echo on parse errors.** If the request line is not valid JSON but starts (after optional whitespace) with
   `{` ws `"id"` ws `:` ws <JSON scalar: integer/number/string/true/false/null> — the reply echoes that id
   instead of null. Replies are produced strictly in request order (FIFO) on the process path; this is now a
   documented guarantee (FORMAT.md §7), so a client may attribute an `id:null` reply to its oldest pending request.
3. **Strict requests.** Allowed keys per op (anything else → `bad_request`):
   hello: id, op · start: id, op, seq · restart: id, op, snapshot · step: id, op, snapshot, event, guards, token.
   `token` is allowed only for timer events (`event` starting with `timer:`) and must be an integer 0…2^53−1
   (mathematical value: `5`, `5.0`, `5e0` and `-0` (= 0) are accepted); `event` must be a non-empty string;
   `timer:` with an empty timer name → `bad_request`.
4. **start with seq.** `{"op":"start","seq":N}` (optional, integer 0…2^53−1) starts a fresh user whose first
   transition gets `seq` N+1 (as if the previous snapshot had seq N). Without `seq` — unchanged (seq 0 → 1).
   HOST.md: hosts resetting an existing user must pass their persistent high-water seq.
5. **Request size limit.** Default 1 MiB (1 048 576 bytes) per request line on the process path
   (`figram-engine --max-request BYTES` to change, 0 = unlimited) and per call on the FFI path
   (`figram_set_max_request(bytes)`, same default). Oversized → `too_large` (id echo per 2 if possible, else null).
6. **Invalid UTF-8.** The process path reads raw bytes; invalid UTF-8 in a request line → `parse_error`
   (no silent U+FFFD replacement). The FFI path validates likewise. Files (`figram check`, engine document) keep
   rejecting invalid UTF-8 as before.
7. **Mutants are test-only.** `figram-engine --mutants/--mutate` require env `FIGRAM_ALLOW_MUTANTS=1`; in mutant mode
   the engine prints a warning to stderr and `hello` returns an extra field `"mutant": "<spec>"`.
8. **Blank lines** on the process path are skipped (unchanged, documented). On the FFI path an empty request returns
   `parse_error` (documented asymmetry).
9. `need` semantics may change (fix-core: only guards that can influence the step) — consumers must not assume
   the old superset.

## Deviations (protocol agent)

Implemented on branch `fix/protocol` (commits d4befe9, db974ec). No item 1–8 was dropped; the points below are
clarifications/narrowings that consumers can rely on (all are covered by `ffi/python-example/protocol_edges.py`).

* **(2) id echo — exact rule.** Echo only if the line starts with `{` ws `"id"` ws `:` ws <scalar> **and the
  scalar is followed by ws and `,` or `}`**; otherwise `null`. (Without the delimiter a line cut by the size limit,
  e.g. `{"id":12|345…`, would echo a wrong id `12`.) The key must be the first key and spelled exactly `"id"`
  (no `\u` escapes). The scalar is echoed **by value**, like a normal reply (`-0` → `0`, `1.5e3` → `15e2`).
  Applies to `parse_error` and `too_large`. For invalid UTF-8 the id is taken from the longest valid-UTF-8 prefix
  (so `{"id":"\xff"…` → `null`).
* **Valid JSON that is not an object** (`[1]`, `"x"`) → `bad_request` with `id: null` (not `parse_error`).
* **Order of checks** for a parsed object: known `op`? → allowed fields → (step/restart) `snapshot` present
  (`bad_request`) and valid (`bad_snapshot`) → event/token (`bad_request`) → guards (`bad_request`) → `seq_overflow`.
  Unknown/missing/non-string `op` is reported before unknown fields. Texts: `нужно поле «op»`,
  `«op» должно быть строкой`, `неизвестная операция «X»`, `неизвестное поле запроса «K»`,
  `«event» должно быть непустой строкой`, `пустое имя таймера в «event»`,
  `поле «token» допустимо только для события таймера`, `«token» должен быть целым от 0 до 9007199254740991`,
  `«seq» должен быть целым от 0 до 9007199254740991`. `seq_overflow` keeps the text `seq_overflow`.
* **(5) size limit** counts the bytes of the line **without** the terminating `\n` (a `\r` before it counts).
  The engine keeps at most the first 64 KiB of an oversized line (for the id echo) and discards the rest while
  reading. Text: `запрос длиннее предела N байт`. A blank line (only space/tab/CR) is skipped even if longer than
  the limit.
* **(6) invalid UTF-8** text: `запрос не в кодировке UTF-8`. `figram json` (test tool) also rejects such lines.
* **(7) mutants:** only `FIGRAM_ALLOW_MUTANTS=1` (exactly `1`) enables them; refusal → exit 2. `--mutate SPEC`
  rejects (exit 2) an unknown kind and a number/name that does not exist in this machine (`redirect:999`,
  `drop-call:nope`); kind-level mutations (`drop-exit`, …) are accepted for any document (the core-compiler
  red-proofs apply them to every directed document). The mutated machine is re-checked with `wf`/`namesOK`
  (failure → exit 1). `"mutant"` is appended after `"states"` in `hello`.
  `--max-request N` may precede `--mutate`.
* **Engine-side helpers for the FFI path** (in `Figram/Engine.lean`, not exported by me — `Api.lean` is fix-ffi's):
  `Engine.handleBytes (mutant : Option String) (limit : Nat) (m : Machine) (b : ByteArray) : String` does
  size limit → UTF-8 → request, i.e. exactly the process-path semantics (call it with `none`; a C caller can build
  the ByteArray with `lean_alloc_sarray` and export it e.g. as `figram_request_bytes`);
  `Engine.errorLine (pre : String) (code : ErrCode) (text : String)`, `Engine.utf8Prefix`, `Engine.tooLargeText`,
  `Engine.invalidUtf8Text`, `Engine.defaultMaxRequest = 1048576`. `Engine.handle m line` (String) keeps its
  signature and now returns codes and the id echo.

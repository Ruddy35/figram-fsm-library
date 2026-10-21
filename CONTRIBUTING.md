# Contributing

Thank you for your interest in Figram. This page describes how to set up a development
environment, which checks a change must pass, and the conventions of the repository.

## Development setup

You need Linux x86_64 and the tools listed in the README, "Building from source": Lean 4.35.0
(through elan and `lean-toolchain`, or `ci/install-lean.sh`), Python 3 with `jsonschema`,
Go 1.23 or newer, gcc and binutils.

```sh
lake build                 # must finish without warnings
bash ffi/build.sh          # the C ABI library, needed by the C and Go tests
```

## Running the checks

```sh
./verify.sh                # the full gate; must exit 0
python3 ci/check_docs.py   # documentation against the code
```

Run the suite that matches your change as well ([docs/testing.md](docs/testing.md)):

* the document format or the JSON text layer — `tests/formats/run_all.sh LOGDIR`;
* the semantics or the compiler — `tests/compiler/run_all.sh ENGINE FIGRAM LOGDIR`;
* the C shim (`ffi/`) — `tests/capi/build.sh`, `tests/capi/sanitizers.sh`,
  `tests/capi/mutants.sh`;
* the Go wrapper — `go test ./...` and `go test -race ./...` in `clients/go`.

CI runs all of them on every push and pull request.

## Conventions

* **English everywhere:** code comments, docstrings, script output, messages and documentation.
* **Stable interface.** Do not change error `code` values, JSON field names, CLI command names or
  exit statuses: clients rely on them. Message texts may change; clients must not parse them.
* **Proofs.** No `sorry`, `admit`, `axiom`, `native_decide`, `implemented_by` or `@[extern]` in the
  Lean sources; theorems may use only `propext`, `Quot.sound` and `Classical.choice`. Changing the
  statement of a pinned theorem means updating `tests/proofs/THEOREMS.expected` (and
  `tests/proofs/Statements.lean`) in the same commit, deliberately. Adding, removing or renaming a
  theorem means regenerating the tracked list:
  `python3 -I tests/proofs/gen_axioms.py . tests/proofs/AllAxioms.lean tests/proofs/theorems.txt`.
* **Tests.** A fix comes with a test that fails without it. Test suites must stay sensitive: they
  run deliberately broken variants and expect them to fail.
* **Documentation.** Keep the documentation in `docs/` in sync with the code; `ci/check_docs.py`
  enforces the parts that can be checked mechanically. Examples that can be run are fenced as
  `sh doctest` (commands after `$ `, continuation lines after `> `, then the expected output;
  `...` matches any text, a last line `[exit N]` expects that exit status), `c doctest` or
  `python doctest` (followed by a `text output` block with the expected output).
* **No machine-specific paths** in the repository; scripts must work from any directory.
* **Generated files are not committed** (`.lake/`, `ffi/out/`, `verify-logs/`, binaries).
  Releases are built by the release workflow ([docs/releasing.md](docs/releasing.md)).

## Commits and pull requests

* One logical change per commit. The subject line is a short imperative sentence in English
  (for example `Reject an empty timer name in step requests`), at most about 72 characters; the
  body explains what changed and why.
* A pull request describes the change, the checks you ran and their result, and must pass CI.

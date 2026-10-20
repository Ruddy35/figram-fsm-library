# Figram — integration of the fix wave (read fully before starting)

The user authorised fixing ALL audit findings and committing freely. Five fix agents worked in parallel on branches
`fix/core`, `fix/protocol`, `fix/ffi`, `fix/client`, `fix/docs`, all based on `fix/base` (= `audit/integration` 8be73ac).
Their reports: `/home/user/figram-audit/fixes/<name>/REPORT.md`; agreed protocol changes: `/home/user/figram-audit/PROTOCOL_CHANGES.md`;
general rules: `/home/user/figram-audit/FIX_BRIEF.md` (same rules apply: no sorry/axiom/native_decide/implemented_by/extern/unsafe,
theorem statements kept unless justified, golden byte-identical unless a fix changes behaviour on purpose, do NOT rebuild or commit `bin/`).

## Goal
Branch `fix/final` (worktree `/home/user/figram-audit-worktrees/fix-final`, created from `fix/base`, .lake copied from fix-core):
merge the fix branches (`git merge --no-ff fix/<name>`; order: ffi, docs, protocol, core — or whatever minimises conflicts),
resolve conflicts keeping BOTH sides' intent, then make the whole tree consistent and green.
`fix/client` is STILL BEING FINISHED by its agent: do not merge it until the orchestrator messages you that it is final
(report: fixes/client/REPORT.md); do everything else meanwhile, then merge it and re-run the client/host checks.
Also bring in the production-like hosts: `git merge --no-ff audit/host-go` and `git merge --no-ff audit/host-python`
(both based on the baseline d69bbaf, they only add `audit/host-go/` and `audit/host-python/`; their docs:
`/home/user/figram-fsm-library/audit-final/PRODUCTION_GO.md`, `PRODUCTION_PYTHON.md`, `audit-final/hosts/HOSTS_SPEC.md`).
Then:

1. TEST-ONLY commits 9778dac and 8be73ac must be gone from the effective tree (fix/ffi reverts them and replaces them with the
   product fix B-01). Check `git diff fix/base~2 fix/final -- ffi/build.sh verify.sh audit/` shows no from-source hack left.
2. Cross-branch consistency (the agents could not see each other's work):
   * clients and validators (`ffi/python-example/figram_client.py`, `protocol.py`, go-example `checkReply`, `host_sim.py`,
     `audit/engine_timers/*`, `audit/host-python`, `audit/host-go`, `tests/formats/*`, `ffi/audit/*`) must accept the new error
     reply `{id,error,code}`, `start` with `seq`, strict requests (no extra keys sent!), `need` that may shrink (fix-core P-14);
   * docs (README, FORMAT, HOST, AUDIT, REVIEW, Api.lean docstrings) describe the final behaviour; fill the README section
     «После оптимизации» with measured numbers of the final build (re-measure with the scripts fix-core/fix-protocol used);
     resolve the items fix-docs left to other owners (see fixes/docs/REPORT.md «Что не сделано»: FORMAT §7 rule 3, double
     caveat, README «Подключение», `--mutate` line) if the owning agent did not;
   * `audit/proofs/theorems.txt` / `AllAxioms.lean` regenerated after the merge:
     `python3 -I audit/proofs/gen_axioms.py . audit/proofs/AllAxioms.lean audit/proofs/theorems.txt`, plus `ci/THEOREMS.expected`
     if new theorems were added; `audit/docs/check_docs.py` (fix-docs) must be green on the final tree;
   * `audit/engine_timers/check_host_contract.py`: extend with `start`+`seq` (P-01 code path) as fix-docs suggested;
   * `examples/golden.jsonl`: if any fix changed behaviour on purpose it was regenerated in that branch; after the merge,
     regenerate once with `figram golden`, diff against each branch's intent, explain every changed line in the report.
3. Full re-verification on the final tree, every command via `/home/user/figram-audit/run.sh <log> ...`, logs in
   `/home/user/figram-audit/fixes/final/LOGS/`:
   * `lake build` from clean `.lake/build` of the project (keep packages): 0 warnings, 0 errors;
   * `audit/proofs/strict_gate.sh` (all theorems, standard axioms only, leanchecker);
   * `ci/` theorem check as verify.sh does it;
   * full gate: strip copies into `$T`, sums, `CGO_LDFLAGS=-lstdc++` only if B-01 still needs it (it should NOT — note it),
     `FIGRAM_SHA256SUMS=$T/SUMS nice -n 10 timeout 2400 ./verify.sh` → expected exit 3 with exactly the hash waiver;
   * `verify-faults.sh` (harness-ci): every injected fault red;
   * audit suites: `audit/core-compiler/run_all.sh`, `audit/engine_timers` driver+suite+mutants, `tests/formats/run_all.sh`,
     `ffi/audit/regress.sh` (+ contract/oom/stress), `go test -race ./...` in go-example and the Go wrapper,
     `ffi/python-example/{client_robustness,client_faults,client_contract,protocol_edges}.py`, client mutation run (fix-client's
     `mutate_client_v2.py`), `check_host_contract.py`, fix-core's perf_scale.py thresholds, fix-protocol's P-10 benchmark;
   * production-like hosts `audit/host-python` and `audit/host-go`: adapt to the final protocol (error `code`, `start` with `seq`,
     P-01 high-water seq on reset) and re-run their corpora and RED matrices (see their README / PRODUCTION_*.md in
     `/home/user/figram-fsm-library/audit-final/`); Go↔Python comparison.
   Anything red: root-cause and fix in `fix/final` (test first if it is a new defect), never weaken a check.
4. Commit in logical steps (merge commits, then "integration: ..." commits). Never push. Do not touch other worktrees or branches.
   Commit messages end with:
   ```
   Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
   Claude-Session: https://claude.ai/code/session_01JgWRtX2jyUidN8gxEhe5qx
   ```

## Known cross-branch items (from the fix reports)
* fix-ffi: set `legacyErrorWithoutCode = false` in `ffi/go-example/main.go` after merging fix/protocol (code becomes mandatory);
  README conflict with fix/docs in the «C-прослойка» table row: keep fix/docs' row + fix-ffi's new «C-ABI» row + fix/docs' «вернуться» row.
  fix-ffi did not touch `REVIEW.md:28` (`lean_mark_persistent`) and `AUDIT.md:55` (`figram_ffi.c ~80 строк`) — fix them.
  Optional (do it if cheap): export `Engine.handleBytes` via `Api.lean` so the FFI path gets the id echo too; otherwise keep the documented asymmetry.
* fix-protocol: verify.sh on fix/protocol alone is red only on Go `TestAuditRequestBadOp` (checkReply without `code`) — resolved by fix/ffi.
  `AUDIT.md:80` (U+FFFD on the C/Go path) is stale after fix/ffi — fix it. verify-prebuilt.sh is red on the OLD bin/ by design until bin/ is rebuilt.
* `audit/core-compiler/run_all.sh` step `mutants` is red already on fix/base (490 survivors on timer rows, probably the harness's liveness model
  predates the P-04 `ownsTimer` fix); fix-core was asked to fix it — check its report; if still red, root-cause it here.
* fix-core (HEAD 42af993): `Main.lean` +4 lines in the `check` branch (warnings to stderr via `Figram.Format.Doc.warnings`
  in new `Figram/Warnings.lean`); `Engine.lean`: only `load` (`m.wfFast`) and `missing` (`neededGuards`, `givenOf`) — fix-protocol's
  mutant re-check should use `Machine.wfFast` (same check, linear; `wfFast_eq`). README «Производительность»: replace the
  "before optimisation" numbers with fix-core's + fix-protocol's tables (see their REPORT.md) and re-measure on fix/final.
  FORMAT §3 (warnings) / §7 rule 3 (`need`) touched by both fix-core and fix-protocol — merge the text by meaning.
  `ptdriver.py`/`ptsuite.py` need model updated by fix-core; fix-protocol adapted the same files for codes — merge both.
  strict_gate now has 139 theorems (fix-core) + fix-protocol's `startAt_*` — regenerate lists after the merge.
* Perf cost of the byte-wise reader (fix-protocol): ~+3.5 µs/request; keep the README note consistent with fix-core's numbers.

## Toolchain, resources
`source /home/user/figram-audit/env.sh` (Lean 4.35.0 from source, gcc/libstdc++). 4 shared cores: `nice -n 10`, timeouts,
no single job > 30 min, no network services, no real Telegram/CRM/payment endpoints. Never `pkill -f` broad patterns —
kill only PIDs you started. Commit early; the container may restart (then: git status, commit finished work, re-run the
interrupted check).

## Report
Final message: ≤ 60 lines summary + full report (Russian) after a line `=== REPORT.md ===`:
1. merge log: conflicts and how each was resolved;
2. table of EVERY audit item (P-01…P-25, I-01…I-18, T-01…T-16, B-01, B-02, D-01…D-08) with final status
   (FIXED / FIXED-DOC / PARTIAL / NOT-FIXED / BY-DESIGN), commit(s) on fix/final, test, log path;
3. table of every check run in step 3: command, exit code, log path, what it proves;
4. golden diff explanation; protocol/behaviour changes visible to hosts; residual risks; what was not done and why.
The harness forbids subagents writing .md report files — return the text; logs/scripts/commits are fine.

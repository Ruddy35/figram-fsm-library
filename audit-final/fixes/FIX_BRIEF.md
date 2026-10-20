# Figram — fix wave brief (read fully before starting)

The user authorised fixing ALL problems found by the audit and committing freely.
Audit results: /home/user/figram-fsm-library/audit-final/ (EXECUTIVE.md, ISSUES_PRODUCT.md,
ISSUES_INTEGRATION.md, ISSUES_TESTS.md, agents/*/REPORT.md). Read the registers for your items.

## Base and branches
* Base for every fix branch: `fix/base` = `audit/integration` HEAD `8be73ac` (baseline d69bbaf + 40 verified commits:
  fixes for P-04, P-08, P-12, P-13, client/Go fixes, hardened verify gate, contract docs). Its top two commits are
  TEST-ONLY toolchain adaptations (9778dac, 8be73ac) — they let verify.sh run on our from-source toolchain; agent
  fix-ffi replaces them with a real product fix (B-01). Keep them in your branch until then.
* Your worktree is pre-created: `/home/user/figram-audit-worktrees/<NAME>` on branch `fix/<NAME>` (warm .lake cache).
  Commit early and often in your branch (one logical change per commit; tests before fixes). Never push; never touch
  other worktrees; never rewrite other branches.
* Commit messages end with:
  ```
  Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01JgWRtX2jyUidN8gxEhe5qx
  ```

## Toolchain and checks
* `source /home/user/figram-audit/env.sh` → Lean 4.35.0 (from source, gcc), lake, leanc, leanchecker.
* Full gate on this toolchain (hash step must be waived because the toolchain differs from the official one):
  ```
  lake build && strip copies of .lake/build/bin/{figram,figram-engine} into $T; (cd $T; sha256sum figram figram-engine) > $T/SUMS
  CGO_LDFLAGS=-lstdc++ FIGRAM_SHA256SUMS=$T/SUMS nice -n 10 timeout 2400 ./verify.sh     # expected exit 3 (one waiver)
  ```
  Exit 3 with exactly the hash waiver = green. Exit 1 = red. Also run the audit suites relevant to your area:
  `audit/proofs/strict_gate.sh` (all theorems, standard axioms only, leanchecker), `audit/core-compiler/run_all.sh`,
  `audit/engine_timers/` driver+suite, `tests/formats/run_all.sh`, `ffi/audit/regress.sh`,
  `ffi/python-example/{client_robustness,client_faults,client_contract,protocol_edges}.py`,
  `audit/engine_timers/check_host_contract.py` (see the agents' REPORT.md for exact invocations).
* Logging: wrap every command you rely on with `/home/user/figram-audit/run.sh <logpath> <cmd...>`; logs go to
  `/home/user/figram-audit/fixes/<NAME>/LOGS/`.
* Resource rules: 4 shared cores; `nice -n 10`, timeouts; no job > 30 min. No network services.

## Rules for fixes
1. For every item: first a regression test that is RED on fix/base (log it), then the fix, then GREEN (log it).
   Design-level items where the fix is documentation: the "test" is the doc/consistency check you state explicitly.
2. Lean: no `sorry`, `admit`, `axiom`, `native_decide`, `implemented_by`, `@[extern]`, `unsafe`, `csimp` tricks.
   Keep the statements of all existing theorems. If a statement MUST change (new hypothesis for a new check),
   prefer moving the check outside the proved function; if impossible, change it minimally, update
   `ci/THEOREMS.expected`, `audit/proofs` pinned statements, and justify in the report. Re-run strict_gate.sh.
3. Behaviour within the documented contract must not change unless the item requires it. `examples/golden.jsonl`
   must stay byte-identical unless your item changes behaviour on purpose — then regenerate with `figram golden`,
   show the diff, justify it.
4. Every new limit/error must be documented in FORMAT.md / HOST.md / README (your area) and covered by a test.
5. Do NOT rebuild or commit `bin/` — the orchestrator rebuilds `bin/` with the official toolchain in CI at the end.
6. If an item cannot be fixed within reasonable effort, stop on it, document precisely why, and propose the path.

## Report
Final message: ≤ 60 lines summary + full report (Russian) after a line `=== REPORT.md ===`:
table `item | change | commit | regression test | RED log | GREEN log | verdict (FIXED / FIXED-DOC / PARTIAL / NOT-FIXED)`,
what changed in behaviour/protocol, risks, what you could not do. The harness forbids subagents writing .md
report files — return the text; logs/scripts/commits are fine.

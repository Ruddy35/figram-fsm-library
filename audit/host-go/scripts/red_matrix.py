#!/usr/bin/env python3
"""RED matrix of the Go host: every invariant must turn FAIL on a deliberately broken
host variant (a real fault in the host code path; the checker is unchanged), and the
correct host must be PASS on the same corpus.

    python3 -I scripts/red_matrix.py OUT_JSON SCRATCH_DIR
"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FGRUN = os.path.join(HERE, "bin", "fgrun")
CORPUS = "/home/user/figram-audit/hosts/shared/corpus"
RUNSH = "/home/user/figram-audit/run.sh"
# HOSTGO_LOGS / HOSTGO_OUT: a re-run (e.g. on a new engine) does not overwrite the audit's evidence
LOGS = os.path.join(os.environ.get("HOSTGO_LOGS", "/home/user/figram-audit/agents/host-go/LOGS"), "red")
OKDIR = os.environ.get("HOSTGO_OUT", "/home/user/figram-audit/hosts/out/go")   # outputs of the correct runs
REPRO = "/home/user/figram-audit/agents/host-go/REPRODUCTIONS"

# (label, broken variant(s), corpus, extra fgrun args, invariants expected to FAIL)
MATRIX = [
    ("no-dedup", "no-dedup", "order-1", [], ["equals_clean_run"]),
    ("ack-before-commit", "ack-before-commit", "order-1", [], ["no_lost_ack"]),
    ("outbox-after-ack", "outbox-after-ack", "order-1", [], ["actions_complete", "send_at_least_once"]),
    ("const-guards/order", "const-guards", "order-1", [], ["guards_match_domain_model"]),
    ("const-guards/booking", "const-guards", "booking-1", [], ["outbox_drained", "guards_match_domain_model"]),
    ("const-guards+no-capacity-check", "const-guards,no-capacity-check", "booking-1", [],
     ["no_overbooking", "domain_model_consistent"]),
    ("no-drain", "no-drain", "order-1", [], ["guards_match_domain_model"]),
    ("no-resource-lock", "no-resource-lock", "booking-1", [], ["guards_match_domain_model"]),
    ("seq-keys-start/order", "seq-keys-start", "order-1", [],
     ["idempotency_keys_unique_lifetime", "seq_plus_one", "one_order_per_payment"]),
    ("seq-keys-start/reminders", "seq-keys-start", "reminders-1", [], ["timer_not_early"]),
    ("random-keys", "random-keys", "order-1", [], ["external_effect_once_per_intent", "one_order_per_payment"]),
    ("no-user-lock+no-dedup", "no-user-lock,no-dedup", "order-1", [], ["equals_clean_run"]),
    ("stale-token-lookup", "stale-token-lookup", "reminders-1", [], ["stale_timer_no_effect"]),
    ("no-validate", "no-validate", "adversarial-1", [], ["malformed_updates_rejected", "other_users_unaffected"]),
    ("memory-timers", "memory-timers", "reminders-1", [], ["restart_reproduces_state", "equals_clean_run"]),
    ("trust-snapshot", "trust-snapshot", "adversarial-1", [],
     ["corrupted_users_repaired", "all_updates_acked", "snapshots_valid_final"]),
    ("no-ack-retry", "no-ack-retry", "@red_ack_retry", [], ["send_duplicates_explained"]),
    ("correct/red_ack_retry", "", "@red_ack_retry", [], []),
    ("no-crash-points", "no-crash-points", "order-1", [], ["crashes_effective"]),
    ("skip-external", "skip-external", "order-1", [], ["external_faults_effective", "external_effect_once_per_intent"]),
    ("no-reply-validate", "no-reply-validate", "adversarial-1", [], ["engine_faults_effective"]),
    ("busy-8000ms (test of test)", "", "failures-1", ["-busy-ms", "8000"], ["db_lock_effective"]),
    # NP-05: after an upgrade an IGNORED step returns the snapshot with timers of the old
    # document (FORMAT §7 rule 2); the strict validator of the first host version poison-loops.
    # On migration-1 this needed the pre-P-04 engine (old timers survived transitions); since
    # the P-04 fix every transition cancels them, so the trigger is the targeted corpus
    # corpora/red_strict_timers-1 (ignored event right after the upgrade). migration-1 stays as
    # an informational row (expected: nothing red).
    ("strict-snapshot-timers", "strict-snapshot-timers", "repo:red_strict_timers-1", [], ["all_updates_acked"]),
    ("correct/red_strict_timers", "", "repo:red_strict_timers-1", [], []),
    ("strict-snapshot-timers/migration-1 (P-04 fixed)", "strict-snapshot-timers", "migration-1", [], []),
    # Unload without waiting for in-flight calls. With the hardened C ABI (P-21: handle cells,
    # quarantine of unloaded handles) a call after the unload gets an error instead of a
    # use-after-free; only an unload racing a call already inside C remains UB, so the soak
    # reloads every 2 ms (≈14 000 reloads in 30 s) — the correct host must survive the same.
    ("unload-no-wait (soak, reload 2ms)", "unload-no-wait", "soak", [], ["no_unexpected_host_death"]),
    ("correct (soak, reload 2ms)", "", "soak", [], []),
]


def run(label, broken, corpus, extra, scratch):
    safe = label.replace("/", "_").replace("+", "_").replace(" ", "_").replace("(", "").replace(")", "")
    out = os.path.join(scratch, "red", safe + ".json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    os.makedirs(LOGS, exist_ok=True)
    cmd = [RUNSH, os.path.join(LOGS, safe), "nice", "-n", "10", "timeout", "900", FGRUN, "run",
           os.path.join(CORPUS, corpus + ".jsonl"), "-out", out, "-quiet", "-ref", os.path.join(scratch, "ref"),
           "-work", os.path.join(scratch, "redwork", safe)] + (["-broken", broken] if broken else []) + extra
    if corpus.startswith("@"):  # targeted corpus of this agent (REPRODUCTIONS), shared corpora untouched
        cmd[cmd.index(os.path.join(CORPUS, corpus + ".jsonl"))] = os.path.join(REPRO, corpus[1:] + ".jsonl")
    if corpus.startswith("repo:"):  # targeted corpus kept in this repository (audit/host-go/corpora)
        cmd[cmd.index(os.path.join(CORPUS, corpus + ".jsonl"))] = os.path.join(HERE, "corpora", corpus[5:] + ".jsonl")
    if corpus == "soak":
        cmd = [RUNSH, os.path.join(LOGS, safe), "nice", "-n", "10", "timeout", "900", FGRUN, "soak", "-secs", "30",
               "-users", "16", "-reload-every", "2ms", "-sample-every", "5s", "-out", out,
               "-work", os.path.join(scratch, "redwork", safe)] + (["-broken", broken] if broken else []) + extra
    p = subprocess.run(cmd, capture_output=True, text=True)
    try:
        d = json.load(open(out))
    except Exception as e:  # noqa: BLE001
        return {"rc": p.returncode, "error": str(e), "log": os.path.join(LOGS, safe + ".log")}
    fails = sorted(k for k, v in d["invariants"].items() if v != "PASS")
    return {"rc": p.returncode, "fails": fails, "details": {k: d["invariant_failures"].get(k, [])[:3] for k in fails},
            "log": os.path.join(LOGS, safe + ".log"), "out": out}


def main():
    out_json, scratch = sys.argv[1], sys.argv[2]
    rows = []
    red_by = {}
    for label, broken, corpus, extra, expect in MATRIX:
        r = run(label, broken, corpus, extra, scratch)
        missing = [e for e in expect if e not in r.get("fails", [])]
        row = {"label": label, "broken": broken, "corpus": corpus, "extra": extra, "expected_red": expect,
               "missing_red": missing, **r}
        rows.append(row)
        for f in r.get("fails", []):
            red_by.setdefault(f, []).append(label)
        print(f"{label:34s} {corpus:14s} rc={r.get('rc')} red={r.get('fails')} missing={missing}", flush=True)
    # every invariant of the correct runs must have been red at least once
    all_inv = set()
    for f in os.listdir(OKDIR):
        if f.endswith(".json") and f[0].isalpha() and not f.startswith(("red_", "compare", "soak")):
            try:
                all_inv |= set(json.load(open(os.path.join(OKDIR, f)))["invariants"])
            except Exception:  # noqa: BLE001
                pass
    never = sorted(all_inv - set(red_by))
    res = {"rows": rows, "red_by_invariant": red_by, "invariants_never_red": never}
    json.dump(res, open(out_json, "w"), ensure_ascii=False, indent=1)
    print("invariants never red:", never)
    bad = [r["label"] for r in rows if r["missing_red"]]
    print("rows with missing expected red:", bad)
    sys.exit(1 if (bad or never) else 0)


if __name__ == "__main__":
    main()

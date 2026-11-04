#!/usr/bin/env python3
"""Markdown table of the recorded runs (runs/<name>/summary.json)."""
import json, os, sys
names = sys.argv[1:]
print("| run | ok | violations | steps | tput/s | crashes | dup skipped tg/outcome/timer | drops | outcomes | stuck (doc trap) | host RSS MB | engine RSS MB | cpu host/engines s |")
print("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
for n in names:
    p = f"runs/{n}/summary.json"
    if not os.path.exists(p):
        continue
    d = json.load(open(p))
    m = d["metrics"]
    v = ", ".join(f"{k} {x['count']}" for k, x in d["violations"].items()) or "—"
    dups = "/".join(str(int(m.get(f"dup.skipped.{k}", 0))) for k in ("tg", "outcome", "timer"))
    drops = ", ".join(f"{k[5:]} {int(x)}" for k, x in m.items() if k.startswith("drop.")) or "—"
    oc = ", ".join(f"{k} {x}" for k, x in d["notes"].get("outcomes", {}).items()) or "—"
    st = ", ".join(f"{x}" for k, x in d["notes"].get("stuck_classes", {}).items()) or "0"
    cpu = d.get("cpu_s", {})
    print(f"| {n} | {'green' if d['ok'] else 'RED'} | {v} | {d['notes'].get('steps')} | {d['throughput_steps_per_s_load']} | "
          f"{d['host_crashes_injected']} | {dups} | {drops} | {oc} | {st} | {d['mem']['host_max_kb']/1024:.0f} | "
          f"{d['mem']['engine_max_kb']/1024:.1f} | {cpu.get('host','')}/{cpu.get('engines','')} |")
